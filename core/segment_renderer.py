"""Bounded segment rendering with ordered results, hardware fallback and cache isolation."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
import math
import os
import shutil
import subprocess
import threading
import time
import csv
import tempfile

from core.timebase import sec_to_frames
from core.video_scanner import probe_video_file


class RenderCancelled(Exception):
    pass


class SegmentRenderError(RuntimeError):
    pass


class HardwareEncoderError(SegmentRenderError):
    pass


def encoder_options(config, encoder, threads=0):
    speed = getattr(config,'render_speed','balanced')
    quality = config.quality
    if encoder == 'h264_nvenc':
        preset = {'fast':'p1','balanced':'p4','quality':'p6'}.get(speed,'p4')
        cq = {'standard':'24','high':'19','very_high':'16'}.get(quality,'19')
        return ['-c:v',encoder,'-preset',preset,'-cq',cq,'-bf','0','-forced-idr','1']
    if encoder == 'h264_qsv':
        preset = 'medium' if speed=='quality' else 'veryfast'
        cq = {'standard':'25','high':'21','very_high':'18'}.get(quality,'21')
        return ['-c:v',encoder,'-preset',preset,'-global_quality',cq]
    if encoder == 'h264_amf':
        cq = {'standard':'24','high':'20','very_high':'17'}.get(quality,'20')
        preset = {'fast':'speed','balanced':'balanced','quality':'quality'}.get(speed,'balanced')
        return ['-c:v',encoder,'-usage','transcoding','-quality',preset,'-rc','cqp','-qp_p',cq,'-qp_i',cq]
    preset = {'fast':'ultrafast','balanced':'veryfast','quality':'medium'}.get(speed,'veryfast')
    crf = {'standard':'23','high':'19','very_high':'16'}.get(quality,'19')
    return ['-c:v','libx264','-preset',preset,'-crf',crf,'-threads',str(threads)]


def worker_count(config, encoder):
    requested = int(getattr(config,'parallel_segments',0) or 0)
    if requested:
        return min(2,max(1,requested))
    w,h=config.width_height
    if encoder in ('h264_nvenc','h264_qsv') and w*h<=1920*1080 and getattr(config,'render_speed','balanced')!='quality':
        return 2
    return 1


def cpu_filter(config):
    w,h = config.width_height
    mode = 'increase' if config.aspect_ratio_mode=='crop' else 'decrease'
    flags = {'fast':'fast_bilinear','balanced':'bicubic','quality':'lanczos'}.get(getattr(config,'render_speed','balanced'),'bicubic')
    scale=f'scale={w}:{h}:force_original_aspect_ratio={mode}:force_divisible_by=2:flags={flags}'
    finish=f'crop={w}:{h}' if mode=='increase' else f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black'
    return scale+','+finish+',setsar=1,format=yuv420p'


@dataclass(frozen=True)
class SegmentPipeline:
    name: str
    input_options: tuple
    video_filter: str


def choose_pipeline(config, encoder, info):
    software=SegmentPipeline('software',(),cpu_filter(config))
    if not getattr(config,'gpu_processing',True) or encoder not in ('h264_nvenc','h264_qsv'):
        return software
    if not info or not info.is_valid or info.codec not in ('h264','hevc','mpeg2video'):
        return software
    if info.pixel_format not in ('yuv420p','yuvj420p','nv12') or info.color_transfer in ('smpte2084','arib-std-b67'):
        return software
    if info.sample_aspect_ratio not in ('1:1','0:1','N/A','') or info.width<=0 or info.height<=0:
        return software
    w,h=config.width_height
    decoder=getattr(config,'decoder_mode','auto')
    if encoder=='h264_qsv' and decoder=='cpu':
        return software
    exact_ratio=(info.width*h==info.height*w)
    # CUDA handles high-resolution scaling; only padding/cropping, when needed,
    # returns to CPU at the reduced resolution. QSV uses the same safe finish.
    scale=max(w/info.width,h/info.height) if config.aspect_ratio_mode=='crop' else min(w/info.width,h/info.height)
    round_size=math.ceil if config.aspect_ratio_mode=='crop' else math.floor
    sw=max(2,round_size(info.width*scale/2)*2)
    sh=max(2,round_size(info.height*scale/2)*2)
    if exact_ratio: sw,sh=w,h
    if encoder=='h264_nvenc':
        algo={'fast':'bilinear','balanced':'bicubic','quality':'lanczos'}.get(getattr(config,'render_speed','balanced'),'bicubic')
        vf=f'scale_cuda=w={sw}:h={sh}:format=nv12:interp_algo={algo}'
        inputs=('-hwaccel','cuda','-hwaccel_output_format','cuda')
        name='cuda'
        # On H.264 sources, CPU decode + GPU resize avoids NVDEC/context overhead
        # on older cards while retaining GPU filtering/encoding. HEVC remains a
        # hardware-decode candidate; advanced settings can override the choice.
        if decoder=='cpu' or (decoder=='auto' and info.codec=='h264'):
            vf='format=nv12,hwupload_cuda,'+vf
            inputs=()
            name='cuda-upload'
    else:
        vf=f'vpp_qsv=w={sw}:h={sh}:format=nv12'
        inputs=('-hwaccel','qsv','-hwaccel_output_format','qsv','-extra_hw_frames','16')
        name='qsv'
    if not exact_ratio:
        finish=f'crop={w}:{h}' if config.aspect_ratio_mode=='crop' else f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black'
        vf+=',hwdownload,format=nv12,'+finish+',setsar=1,format=yuv420p'
        name+='-hybrid'
    else:
        vf+=',setsar=1'
    return SegmentPipeline(name,inputs,vf)


class SegmentRenderer:
    def __init__(self, engine, encoder, options, directory):
        self.engine, self.config, self.encoder = engine, engine.config, encoder
        self.directory, self.options = directory, options
        self.workers=worker_count(self.config,encoder)
        self.threads=max(1,min(4,(os.cpu_count() or 2)//self.workers))
        self.options=encoder_options(self.config,encoder,self.threads) if encoder=='libx264' else options
        self._lock=threading.RLock()
        self._cache_lock=getattr(engine.render_cache,'_lock',threading.RLock())
        self._processes=set()
        self._aborted=threading.Event()
        self._failure=None
        self._metadata=dict(engine.source_metadata)
        self._software_sources=set()
        self._disabled_pipelines=engine.disabled_gpu_pipelines
        self.stats={'cache_hits':0,'rendered':0,'hardware_segments':0,'software_fallbacks':0,
                    'workers':self.workers,'encoder':encoder,'batched_segments':0,'ffmpeg_launches':0,'pipelines':{}}
        self._progress_seconds={}
        self._total_seconds=1
        self._last_percent=0
        self._video_label=''

    def report_progress(self,key,seconds,message):
        with self._lock:
            self._progress_seconds[key]=max(self._progress_seconds.get(key,0),seconds)
            percent=min(85,round(sum(self._progress_seconds.values())/self._total_seconds*85,1))
            self._last_percent=max(self._last_percent,percent)
            self.engine.on_progress(self._last_percent,message)

    def stopped(self):
        return self.engine._is_cancelled or self._aborted.is_set()

    def cancel(self):
        self._aborted.set()
        with self._lock:
            processes=list(self._processes)
        from core.process_runner import ProcessRunner
        for process in processes:
            try: ProcessRunner.kill_process_tree(process.pid,proc=process)
            except Exception:
                try: process.kill()
                except Exception: pass

    def _pause_gate(self):
        while self.engine._is_paused and not self.stopped():
            time.sleep(.05)
        if self.stopped(): raise RenderCancelled()

    def command(self,clip,path,pipeline,info=None):
        count=max(1,int(getattr(clip,'frame_count',0) or sec_to_frames(clip.effective_duration,self.config.fps)))
        # Metadata's clip frame count belongs to the selected output FPS.
        if getattr(clip,'trim_end_sec',None) is not None:
            count=max(1,sec_to_frames(clip.effective_duration,self.config.fps))
        command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y',
                 '-threads',str(self.threads),*pipeline.input_options,
                 '-ss',str(clip.start_sec),'-i',clip.source_video_path]
        if self.config.audio_mode=='keep' and info is not None and not info.has_audio:
            command+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        command+=['-map','0:v:0','-filter_threads','1','-vf',pipeline.video_filter,
                  '-r',str(self.config.fps),'-fps_mode','cfr',
                  '-t',str(count/float(self.config.fps)),'-frames:v',str(count),*self.options]
        if self.config.audio_mode=='keep':
            command+=['-map','1:a:0' if info is not None and not info.has_audio else '0:a:0',
                      '-c:a','aac','-b:a','192k','-ar','48000','-ac','2']
        else:
            command+=['-an']
        command+=['-video_track_timescale','90000',path]
        return command

    def run_command(self,command,on_time=None):
        errors=tempfile.TemporaryFile() if on_time else None
        with self._lock:
            if self.stopped():
                if errors: errors.close()
                raise RenderCancelled()
            try:
                process=subprocess.Popen(command,stdout=subprocess.PIPE if on_time else subprocess.DEVNULL,
                    stderr=errors if on_time else subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            except BaseException:
                if errors: errors.close()
                raise
            self._processes.add(process)
            self.stats['ffmpeg_launches']+=1
        try:
            if on_time:
                for line in iter(process.stdout.readline,b''):
                    if line.startswith(b'frame='):
                        try: on_time(int(line.split(b'=',1)[1])/float(self.config.fps))
                        except ValueError: pass
                process.stdout.close()
                process.wait()
                errors.seek(0,os.SEEK_END)
                errors.seek(max(0,errors.tell()-65536))
                stderr=errors.read()
            else:
                _,stderr=process.communicate()
            if self.stopped(): raise RenderCancelled()
            return process.returncode,(stderr or b'').decode('utf-8',errors='replace')[-1600:]
        except BaseException:
            if process.poll() is None:
                from core.process_runner import ProcessRunner
                ProcessRunner.kill_process_tree(process.pid,proc=process)
                process.wait(timeout=3)
            raise
        finally:
            with self._lock: self._processes.discard(process)
            if errors: errors.close()

    def _key(self,clip,pipeline):
        w,h=self.config.width_height
        return self.engine.render_cache.compute_key(source_path=clip.source_video_path,
            start_sec=clip.start_sec,duration=clip.effective_duration,width=w,height=h,fps=self.config.fps,
            aspect_ratio_mode=self.config.aspect_ratio_mode,encoder=self.encoder,audio_mode=self.config.audio_mode,
            quality=self.config.quality,encoder_opts=self.options,
            pipeline_version='v4:'+pipeline.name+':'+pipeline.video_filter)

    def _try_cache(self,clip,path,pipeline):
        cache=self.engine.render_cache
        if cache and cache.enabled:
            try:
                with self._cache_lock:
                    key=self._key(clip,pipeline)
                    cached=cache.get(key)
                    if cached and cache.verify_entry(cached):
                        shutil.copy2(cached,path)
                        with self._lock: self.stats['cache_hits']+=1
                        self.engine.on_log('INFO',f'Cache: {clip.source_video_name} · {clip.formatted_start}')
                        return True
            except OSError:
                pass
        return False

    def render_clip(self,index,clip):
        self._pause_gate()
        clip.status='Đang cắt'
        path=os.path.join(self.directory,f'seg_{index:05d}.mp4')
        info=self._metadata.get(clip.source_video_path)
        pipeline=choose_pipeline(self.config,self.encoder,info)
        capability=(self.encoder,getattr(info,'codec',''),getattr(info,'pixel_format',''),pipeline.name)
        with self._lock:
            if clip.source_video_path in self._software_sources or capability in self._disabled_pipelines:
                pipeline=SegmentPipeline('software',(),cpu_filter(self.config))
        pipelines=[pipeline]
        if pipeline.name!='software':
            pipelines.append(SegmentPipeline('software',(),cpu_filter(self.config)))
        for attempt,current in enumerate(pipelines):
            if self.stopped(): raise RenderCancelled()
            if self._try_cache(clip,path,current):
                return path
            code,error=self.run_command(self.command(clip,path,current,info))
            if code==0 and os.path.isfile(path) and os.path.getsize(path)>0:
                cache=self.engine.render_cache
                if cache and cache.enabled:
                    try:
                        with self._cache_lock: cache.put(self._key(clip,current),path)
                    except OSError: pass
                with self._lock:
                    self.stats['rendered']+=1
                    if current.name!='software': self.stats['hardware_segments']+=1
                    self.stats['pipelines'][current.name]=self.stats['pipelines'].get(current.name,0)+1
                return path
            if attempt<len(pipelines)-1:
                with self._lock:
                    self._software_sources.add(clip.source_video_path)
                    self._disabled_pipelines.add(capability)
                    self.stats['software_fallbacks']+=1
                self.engine.on_log('WARNING',f'Không dùng được giải mã/đổi kích thước GPU cho {clip.source_video_name}; đang thử lại, giữ bộ mã hóa {self.encoder}. {error[-240:]}')
        clip.status='Lỗi'
        self._failure=HardwareEncoderError(error) if self.encoder!='libx264' else SegmentRenderError(error)
        self.cancel()
        raise self._failure

    def groups(self,plan):
        """Adjacent compatible clips share one decoder/encoder initialization."""
        groups=[]
        signature=None
        duration=0
        for index,clip in enumerate(plan):
            info=self._metadata.get(clip.source_video_path)
            pipeline=choose_pipeline(self.config,self.encoder,info)
            eligible=(self.encoder=='h264_nvenc' and self.config.audio_mode!='keep' and
                      not self.engine.batch_render_disabled and info and info.codec=='h264' and
                      bool(info.time_base) and abs(info.fps-float(self.config.fps))<.001 and
                      pipeline.name.startswith('cuda') and clip.effective_duration<=30)
            current=(info.width,info.height,info.fps,info.pixel_format,info.time_base,
                     info.sample_aspect_ratio,info.has_audio,pipeline.name) if eligible else None
            if (current is not None and groups and signature==current and len(groups[-1])<4
                    and duration+clip.effective_duration<=30):
                groups[-1].append(index)
                duration+=clip.effective_duration
            else:
                groups.append([index]); duration=clip.effective_duration
            signature=current
        return groups

    def render_group(self,indices,plan):
        self._pause_gate()
        if len(indices)<2 or self.engine.batch_render_disabled:
            return [self.render_clip(index,plan[index]) for index in indices]
        clips=[plan[index] for index in indices]
        info=self._metadata[clips[0].source_video_path]
        pipeline=choose_pipeline(self.config,self.encoder,info)
        capability=(self.encoder,info.codec,info.pixel_format,pipeline.name)
        if capability in self._disabled_pipelines:
            return [self.render_clip(index,plan[index]) for index in indices]
        destinations=[os.path.join(self.directory,f'seg_{index:05d}.mp4') for index in indices]
        hits=[self._try_cache(clip,path,pipeline) for clip,path in zip(clips,destinations)]
        if any(hits):
            return [path if hit else self.render_clip(index,plan[index])
                    for index,path,hit in zip(indices,destinations,hits)]
        manifest=os.path.join(self.directory,f'batch_{indices[0]}.ffconcat')
        listing=os.path.join(self.directory,f'batch_{indices[0]}.csv')
        pattern=os.path.join(self.directory,f'batch_{indices[0]}_%03d.mp4')
        counts=[max(1,sec_to_frames(clip.effective_duration,self.config.fps)) for clip in clips]
        durations=[n/float(self.config.fps) for n in counts]
        with open(manifest,'w',encoding='utf-8',newline='\n') as handle:
            handle.write('ffconcat version 1.0\n')
            for clip,duration in zip(clips,durations):
                path=os.path.abspath(clip.source_video_path).replace('\\','/').replace("'","'\\''")
                # A tiny margin avoids losing the first frame when an exact
                # frame boundary is rounded to FFmpeg's microsecond time base.
                start=max(0,clip.start_sec-.0001)
                end=clip.start_sec+duration-.0001
                handle.write(f"file '{path}'\ninpoint {start:.9f}\noutpoint {end:.9f}\nduration {duration:.9f}\n")
        boundaries=[]
        cursor=0
        for duration in durations[:-1]:
            cursor+=duration; boundaries.append(f'{cursor:.9f}')
        command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y','-xerror',
                 '-copyts','-threads',str(self.threads),*pipeline.input_options,
                 '-f','concat','-safe','0','-segment_time_metadata','1','-i',manifest,
                 '-map','0:v:0','-an','-filter_threads','1',
                 '-vf','select=concatdec_select,setpts=PTS-STARTPTS,'+pipeline.video_filter,
                 '-r',str(self.config.fps),'-fps_mode','cfr','-frames:v',str(sum(counts)),*self.options,
                 '-force_key_frames',','.join(boundaries),'-progress','pipe:1','-nostats',
                 '-f','segment','-segment_format','mp4','-segment_format_options','video_track_timescale=90000',
                 '-segment_times',','.join(boundaries),'-segment_time_delta',str(.5/float(self.config.fps)),
                 '-reset_timestamps','1','-segment_list',listing,'-segment_list_type','csv',pattern]
        total=sum(durations)
        def progress(seconds):
            self.report_progress(indices[0],min(seconds,total),f'Video #{self._video_label}: Đang xử lý các đoạn video…')
        code,error=self.run_command(command,on_time=progress)
        parts=[pattern.replace('%03d',f'{i:03d}') for i in range(len(clips))]
        valid=False
        if code==0 and os.path.isfile(listing):
            try:
                with open(listing,newline='',encoding='utf-8') as handle:
                    rows=list(csv.reader(handle))
                valid=(len(rows)==len(clips) and all(
                    abs((float(row[2])-float(row[1]))-duration)<.5/float(self.config.fps)
                    for row,duration in zip(rows,durations)))
                valid=valid and all(os.path.isfile(path) and os.path.getsize(path)>8 for path in parts)
            except (OSError,ValueError,IndexError,csv.Error):
                valid=False
        if not valid:
            self.engine.batch_render_disabled=True
            self.engine.on_log('WARNING','Nguồn không phù hợp để xử lý cùng lượt; đang chuyển sang từng đoạn. '+error[-240:])
            return [self.render_clip(index,plan[index]) for index in indices]
        for clip,part,path in zip(clips,parts,destinations):
            os.replace(part,path)
            cache=self.engine.render_cache
            if cache and cache.enabled:
                try:
                    with self._cache_lock: cache.put(self._key(clip,pipeline),path)
                except OSError: pass
        with self._lock:
            self.stats['rendered']+=len(clips)
            self.stats['hardware_segments']+=len(clips)
            self.stats['batched_segments']+=len(clips)
            self.stats['pipelines'][pipeline.name]=self.stats['pipelines'].get(pipeline.name,0)+len(clips)
        return destinations

    def render(self,plan,video_index,total_videos):
        # Probe once per source. GPU use and aspect fitting are selected from real
        # stream properties; unusual pixel formats keep the compatible CPU path.
        if self.config.audio_mode=='keep' or (self.encoder in ('h264_nvenc','h264_qsv') and self.config.gpu_processing):
            for source in dict.fromkeys(c.source_video_path for c in plan):
                self._pause_gate()
                if source not in self._metadata or not self._metadata[source].pixel_format:
                    self._metadata[source]=probe_video_file(source)
                    self.engine.source_metadata[source]=self._metadata[source]
        self.engine.on_log('INFO',f'Xuất bằng {self.encoder} · {self.workers} đoạn song song · chế độ {self.config.render_speed}.')
        groups=self.groups(plan)
        self._total_seconds=max(.001,sum(clip.effective_duration for clip in plan))
        self._video_label=f'{video_index}/{total_videos}'
        outputs=[None]*len(plan)
        completed=0
        next_index=0
        with ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='remix-render') as pool:
            pending={}
            try:
                while pending or next_index<len(groups):
                    self._pause_gate()
                    while next_index<len(groups) and len(pending)<self.workers:
                        index=next_index; next_index+=1
                        pending[pool.submit(self.render_group,groups[index],plan)]=index
                    done,_=wait(pending,timeout=.1,return_when=FIRST_COMPLETED)
                    # Inspect failures before accepting other completions in this set.
                    for future in done:
                        error=future.exception()
                        if error: raise error
                    for future in done:
                        index=pending.pop(future)
                        for clip_index,path in zip(groups[index],future.result()):
                            outputs[clip_index]=path
                            clip=plan[clip_index]; clip.status='Hoàn tất'
                            completed+=1
                            self.engine.on_segment_done(completed,len(plan),clip)
                        self.report_progress(groups[index][0],sum(plan[i].effective_duration for i in groups[index]),
                            f'Video #{video_index}/{total_videos}: Đã xử lý {completed}/{len(plan)} đoạn ({self.workers} tác vụ)')
            except BaseException as error:
                self.cancel()
                for future in pending: future.cancel()
                raise self._failure or error
        return outputs
