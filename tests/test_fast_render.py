"""Rendering scheduling, cancellation, cache and profile regression coverage."""
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from core.config import OutputConfig,SegmentInfo,VideoInfo
from core.render_cache import RenderCache
from core.video_processor import VideoProcessorEngine
from core.segment_renderer import (SegmentRenderer,SegmentPipeline,RenderCancelled,
    HardwareEncoderError,encoder_options,choose_pipeline,worker_count)


class FastRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root=Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def clip(self,index=0,duration=1):
        return SegmentInfo(str(index),str(self.root/'source.mp4'),'source.mp4',index,index+duration,
                           duration,index+1,plan_position=index+1)

    def info(self,**changes):
        info=VideoInfo(str(self.root/'source.mp4'),'source.mp4',60,1920,1080,30,'h264',False,100,
                       pixel_format='yuv420p',time_base='1/15360')
        for name,value in changes.items(): setattr(info,name,value)
        return info

    def engine(self,**changes):
        data=dict(hw_accel='cpu',resolution='640x360',audio_mode='none',parallel_segments=2,gpu_processing=False)
        data.update(changes)
        return VideoProcessorEngine(OutputConfig(**data),render_cache=RenderCache(enabled=False))

    def renderer(self,engine,encoder='libx264'):
        return SegmentRenderer(engine,encoder,encoder_options(engine.config,encoder),str(self.root))

    def test_parallel_completion_preserves_plan_order_and_stays_bounded(self):
        engine=self.engine()
        renderer=self.renderer(engine)
        lock=threading.Lock(); active=0; maximum=0; completed=[]
        def work(index,clip):
            nonlocal active,maximum
            with lock:
                active+=1; maximum=max(maximum,active)
            time.sleep(.08 if index==0 else .01)
            with lock:
                active-=1; completed.append(index)
            return f'path-{index}'
        with patch.object(renderer,'render_clip',side_effect=work):
            paths=renderer.render([self.clip(i) for i in range(5)],1,1)
        self.assertEqual(paths,[f'path-{i}' for i in range(5)])
        self.assertNotEqual(completed,list(range(5)))
        self.assertEqual(maximum,2)

    def test_pause_blocks_new_work_until_resume(self):
        engine=self.engine(); renderer=self.renderer(engine)
        engine.pause(); calls=[]; result=[]
        def execute():
            result.extend(renderer.render([self.clip()],1,1))
        with patch.object(renderer,'render_clip',side_effect=lambda i,c: calls.append(i) or 'done'):
            thread=threading.Thread(target=execute); thread.start()
            time.sleep(.08)
            self.assertEqual(calls,[])
            engine.resume(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result,['done'])

    def test_cancel_kills_every_active_ffmpeg_and_stops_scheduling(self):
        engine=self.engine(); renderer=self.renderer(engine)
        engine._segment_renderer=renderer
        created={}; created_lock=threading.Lock(); errors=[]
        class Process:
            def __init__(self,*args,**kwargs):
                self.returncode=None; self.done=threading.Event()
                with created_lock:
                    self.pid=10000+len(created); created[self.pid]=self
            def communicate(self):
                self.done.wait(3); self.returncode=-9
                return b'',b'cancelled'
            def poll(self): return self.returncode
            def wait(self,timeout=None): self.done.wait(timeout); self.returncode=-9; return -9
            def kill(self): self.done.set()
        def kill(pid,proc=None): created[pid].done.set()
        def execute():
            try: renderer.render([self.clip(i) for i in range(6)],1,1)
            except BaseException as error: errors.append(error)
        with patch('core.segment_renderer.subprocess.Popen',Process), \
             patch('core.process_runner.ProcessRunner.kill_process_tree',side_effect=kill):
            thread=threading.Thread(target=execute); thread.start()
            deadline=time.monotonic()+2
            while len(created)<2 and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(len(created),2)
            engine.cancel(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(created),2)
        self.assertTrue(all(p.done.is_set() for p in created.values()))
        self.assertTrue(any(isinstance(error,RenderCancelled) for error in errors))
        self.assertFalse(renderer._processes)

    def test_gpu_decode_failure_retries_with_same_encoder_and_cpu_filters(self):
        engine=self.engine(gpu_processing=True,decoder_mode='gpu')
        info=self.info(time_base='')
        engine.source_metadata[info.path]=info
        renderer=self.renderer(engine,'h264_nvenc')
        commands=[]
        def execute(command,*args,**kwargs):
            commands.append(command)
            if '-hwaccel' in command: return 1,'Unsupported decoder'
            Path(command[-1]).write_bytes(b'0000ftyp'+b'x'*100)
            return 0,''
        with patch.object(renderer,'run_command',side_effect=execute):
            paths=renderer.render([self.clip(0),self.clip(1)],1,1)
        self.assertEqual(len(paths),2)
        self.assertTrue(any('-hwaccel' in cmd for cmd in commands))
        self.assertTrue(any('-hwaccel' not in cmd for cmd in commands))
        self.assertTrue(all(cmd[cmd.index('-c:v')+1]=='h264_nvenc' for cmd in commands))

    def test_fatal_gpu_failure_retries_all_segments_with_one_cpu_profile(self):
        engine=self.engine()
        configs=[]; concat_contents=[]
        class Renderer:
            def __init__(self,owner,encoder,opts,directory):
                self.encoder,self.directory=encoder,Path(directory)
                configs.append(encoder)
                self.stats={'rendered':2,'cache_hits':0,'hardware_segments':0}
            def render(self,plan,*args):
                paths=[self.directory/f'seg_{i:05d}.mp4' for i in range(len(plan))]
                if self.encoder=='h264_nvenc':
                    paths[0].write_bytes(b'gpu')
                    raise HardwareEncoderError('GPU unavailable')
                for path in paths: path.write_bytes(b'cpu')
                return [str(path) for path in paths]
        class Process:
            def __init__(self,command,*args,**kwargs):
                self.returncode=0
                self.stdout=io.BytesIO()
                self.stderr=io.BytesIO()
                if '-i' in command:
                    directory = Path(command[command.index('-i') + 1]).parent
                else:
                    directory = Path(command[-1]).parent
                concat_contents.extend(p.read_bytes() for p in sorted(directory.glob('seg_*.mp4')))
                Path(command[-1]).write_bytes(b'concat')
            def communicate(self): return b'',b''
            def wait(self,timeout=None): return 0
        validation=SimpleNamespace(is_valid=True,actual_duration=2,actual_width=640,actual_height=360,actual_fps=30)
        def publish(**kwargs):
            os.replace(kwargs['staging_path'],kwargs['final_destination_path'])
            return True,''
        with patch('core.video_processor.SegmentRenderer',Renderer), \
             patch.object(engine,'_determine_encoder',side_effect=[('h264_nvenc',[]),('libx264',[])]), \
             patch('core.video_processor.subprocess.Popen',Process), \
             patch('core.video_processor.OutputValidator.validate_video',return_value=validation), \
             patch('core.video_processor.OutputCommit.publish_file',side_effect=publish):
            self.assertTrue(engine.process_video_plan([self.clip(0),self.clip(1)],str(self.root/'out.mp4'),1,1))
        self.assertEqual(configs,['h264_nvenc','libx264'])
        self.assertEqual(concat_contents,[b'cpu',b'cpu'])

    def test_cache_keys_separate_speed_and_processing_paths(self):
        engine=self.engine(gpu_processing=True)
        engine.render_cache=RenderCache(str(self.root/'cache'))
        renderer=self.renderer(engine,'h264_nvenc')
        pipeline=choose_pipeline(engine.config,'h264_nvenc',self.info())
        software=SegmentPipeline('software',(),'scale=640:360')
        gpu_key=renderer._key(self.clip(),pipeline)
        self.assertNotEqual(gpu_key,renderer._key(self.clip(),software))
        fast=self.engine(gpu_processing=True,render_speed='fast')
        fast.render_cache=engine.render_cache
        fast_renderer=self.renderer(fast,'h264_nvenc')
        self.assertNotEqual(gpu_key,fast_renderer._key(self.clip(),choose_pipeline(fast.config,'h264_nvenc',self.info())))

    def test_warm_clip_cache_avoids_starting_ffmpeg(self):
        engine=self.engine()
        engine.render_cache=RenderCache(str(self.root/'cache'))
        renderer=self.renderer(engine)
        def execute(command):
            Path(command[-1]).write_bytes(b'0000ftyp'+b'x'*100)
            return 0,''
        with patch.object(renderer,'run_command',side_effect=execute): renderer.render_clip(0,self.clip())
        with patch.object(renderer,'run_command',side_effect=AssertionError('cache should avoid subprocess')):
            path=renderer.render_clip(1,self.clip())
        self.assertTrue(Path(path).exists())
        self.assertEqual(renderer.stats['cache_hits'],1)

    def test_hdr_and_10bit_sources_keep_compatible_processing(self):
        config=self.engine(gpu_processing=True).config
        self.assertEqual(choose_pipeline(config,'h264_nvenc',self.info(pixel_format='yuv420p10le')).name,'software')
        self.assertEqual(choose_pipeline(config,'h264_nvenc',self.info(color_transfer='smpte2084')).name,'software')
        self.assertNotEqual(choose_pipeline(config,'h264_nvenc',self.info()).name,'software')

    def test_groups_are_bounded_and_do_not_cross_incompatible_sources(self):
        engine=self.engine(gpu_processing=True)
        info=self.info(); engine.source_metadata[info.path]=info
        renderer=self.renderer(engine,'h264_nvenc')
        clips=[self.clip(i,8) for i in range(8)]
        renderer._metadata=dict(engine.source_metadata)
        groups=renderer.groups(clips)
        self.assertEqual([i for group in groups for i in group],list(range(8)))
        self.assertTrue(any(len(group)>1 for group in groups))
        self.assertTrue(all(len(group)<=4 and sum(clips[i].effective_duration for i in group)<=30 for group in groups))
        other=self.info(path=str(self.root/'other.mp4'),width=1280,height=720)
        renderer._metadata[other.path]=other
        clips[1].source_video_path=other.path
        groups=renderer.groups(clips)
        self.assertTrue(all(not(0 in group and 1 in group) for group in groups))

    def test_keep_audio_inserts_silence_for_a_source_without_audio(self):
        engine=self.engine(audio_mode='keep')
        renderer=self.renderer(engine)
        command=renderer.command(self.clip(),str(self.root/'output.mp4'),
            SegmentPipeline('software',(),'scale=640:360'),self.info(has_audio=False))
        self.assertIn('anullsrc=r=48000:cl=stereo',command)
        self.assertIn('1:a:0',command)

    def test_auto_worker_count_avoids_competing_unlimited_cpu_encoders(self):
        config=OutputConfig(parallel_segments=0,resolution='1920x1080')
        self.assertEqual(worker_count(config,'libx264'),1)
        self.assertEqual(worker_count(config,'h264_nvenc'),2)
        config.resolution='3840x2160'
        self.assertEqual(worker_count(config,'h264_nvenc'),1)

    def test_streamed_progress_uses_encoded_frames_and_releases_handles(self):
        renderer=self.renderer(self.engine())
        class Process:
            def __init__(self,*args,**kwargs):
                self.pid=99999; self.stdout=io.BytesIO(b'frame=30\nprogress=continue\nframe=90\nprogress=end\n'); self.returncode=0
            def poll(self): return 0
            def wait(self,timeout=None): return 0
        values=[]
        with patch('core.segment_renderer.subprocess.Popen',Process):
            result=renderer.run_command(['ffmpeg'],on_time=values.append)
        self.assertEqual(result,(0,''))
        self.assertEqual(values,[1,3])
        self.assertFalse(renderer._processes)

    def test_auto_decoder_uses_measured_h264_path_and_keeps_hevc_on_gpu(self):
        config=self.engine(gpu_processing=True).config
        h264=choose_pipeline(config,'h264_nvenc',self.info())
        self.assertEqual(h264.name,'cuda-upload')
        self.assertIn('hwupload_cuda',h264.video_filter)
        self.assertFalse(h264.input_options)
        hevc=choose_pipeline(config,'h264_nvenc',self.info(codec='hevc'))
        self.assertEqual(hevc.name,'cuda')
        self.assertIn('-hwaccel',hevc.input_options)

    def test_explicit_decoder_overrides_automatic_choice(self):
        config=self.engine(gpu_processing=True,decoder_mode='gpu').config
        self.assertEqual(choose_pipeline(config,'h264_nvenc',self.info()).name,'cuda')
        config.decoder_mode='cpu'
        self.assertEqual(choose_pipeline(config,'h264_nvenc',self.info(codec='hevc')).name,'cuda-upload')
        self.assertEqual(choose_pipeline(config,'h264_qsv',self.info()).name,'software')
