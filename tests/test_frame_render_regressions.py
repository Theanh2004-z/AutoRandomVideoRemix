"""Actual FFmpeg images and asynchronous preview shutdown regressions."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest

from core.config import OutputConfig, SegmentInfo
from core.segment_renderer import cpu_filter, choose_pipeline, SegmentPipeline, SegmentRenderer
from core.render_cache import RenderCache
from core.video_scanner import probe_video_file
from core.video_processor import VideoProcessorEngine
from ui.dashboard import PreviewWorker
import tests.test_guided_workflow as guided


class ActualFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
            raise unittest.SkipTest('FFmpeg and ffprobe required')
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.sar = cls.root/'anamorphic.mp4'
        cls.rotated = cls.root/'rotated.mp4'
        cls.square = cls.root/'square.mp4'
        # The stored pixels are 4:3, but SAR 4:3 makes the displayed image 16:9.
        # Its colored edge bands must survive an exact 16:9 fit/crop.
        bands = ('color=c=green:s=144x108:r=2,'
                 'drawbox=x=0:y=0:w=iw:h=12:color=red:t=fill,'
                 'drawbox=x=0:y=96:w=iw:h=12:color=blue:t=fill,setsar=4/3')
        cls.ffmpeg('-f','lavfi','-i',bands,'-t','2','-c:v','libx264',cls.sar)
        marked=('color=c=white:s=320x180:r=2,'
                'drawbox=x=0:y=0:w=40:h=ih:color=red:t=fill,'
                'drawbox=x=280:y=0:w=40:h=ih:color=blue:t=fill')
        cls.ffmpeg('-f','lavfi','-i',marked,'-t','2','-c:v','libx264',cls.square)
        cls.ffmpeg('-display_rotation','90','-i',cls.square,'-c','copy',cls.rotated)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def ffmpeg(*args):
        return subprocess.run(['ffmpeg','-v','error','-nostdin','-y',*map(str,args)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

    def render(self, source, resolution, mode):
        cfg=OutputConfig(resolution=resolution,aspect_ratio_mode=mode)
        raw=self.ffmpeg('-i',source,'-vf',cpu_filter(cfg),'-frames:v','1',
                        '-f','rawvideo','-pix_fmt','rgb24','pipe:1').stdout
        w,h=cfg.width_height
        self.assertEqual(len(raw), w*h*3)
        return lambda x,y: tuple(raw[(y*w+x)*3:(y*w+x)*3+3])

    def test_probe_reads_display_matrix_and_disables_gpu_geometry(self):
        info=probe_video_file(str(self.rotated))
        self.assertTrue(info.is_valid)
        self.assertEqual(info.rotation % 360, 90)
        self.assertEqual((info.display_width,info.display_height),(180,320))
        for encoder in ['h264_nvenc','h264_qsv']:
            pipeline=choose_pipeline(OutputConfig(aspect_ratio_mode='crop'),encoder,info)
            self.assertEqual(pipeline.name,'software')

    def test_rotated_source_can_fill_landscape_without_added_borders(self):
        pixel=self.render(self.rotated,'320x180','crop')
        for x,y in [(0,0),(319,0),(0,179),(319,179)]:
            self.assertGreater(min(pixel(x,y)),240)

    def test_anamorphic_exact_display_ratio_keeps_edge_bands(self):
        for mode in ['fit','crop']:
            with self.subTest(mode=mode):
                pixel=self.render(self.sar,'320x180',mode)
                red=pixel(160,3); blue=pixel(160,176)
                self.assertGreater(red[0],200);self.assertLess(max(red[1:]),40)
                self.assertGreater(blue[2],200);self.assertLess(max(blue[:2]),40)

    def test_anamorphic_fit_into_portrait_uses_display_ratio(self):
        pixel=self.render(self.sar,'180x320','fit')
        self.assertLess(max(pixel(90,100)),20)  # Correct 16:9 content starts near y=109.
        self.assertGreater(pixel(90,113)[0],180)

    def test_preview_keeps_same_anamorphic_edge_bands(self):
        for mode in ['fit','crop']:
            with self.subTest(mode=mode):
                images=[]
                worker=PreviewWorker(str(self.sar),0,None,width=320,height=180,aspect_ratio_mode=mode)
                worker.ready.connect(images.append)
                worker.run()  # Execute the real production command synchronously.
                self.assertEqual(len(images),1)
                image=images[0]
                self.assertEqual((image.width(),image.height()),(480,270))
                self.assertGreater(image.pixelColor(240,5).red(),200)
                self.assertGreater(image.pixelColor(240,264).blue(),200)

    def test_corrected_filter_does_not_reuse_old_cache_geometry(self):
        cfg=OutputConfig(resolution='320x180',aspect_ratio_mode='crop')
        engine=VideoProcessorEngine(cfg,render_cache=RenderCache(str(self.root/'cache'),enabled=False))
        renderer=SegmentRenderer(engine,'libx264',[],str(self.root))
        clip=SegmentInfo('clip',str(self.sar),'anamorphic.mp4',0,1,1,1)
        corrected=cpu_filter(cfg)
        legacy=corrected.replace(':reset_sar=1','')
        self.assertNotEqual(renderer._key(clip,SegmentPipeline('software',(),corrected)),
                            renderer._key(clip,SegmentPipeline('software',(),legacy)))


class PreviewLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guided.GuidedWorkflowTests.setUpClass.__func__(cls)
    setUp=guided.GuidedWorkflowTests.setUp
    tearDown=guided.GuidedWorkflowTests.tearDown
    load_sources=guided.GuidedWorkflowTests.load_sources
    prepare=guided.GuidedWorkflowTests.prepare
    await_preparation=guided.GuidedWorkflowTests.await_preparation
    await_condition=guided.GuidedWorkflowTests.await_condition

    def controlled_preview(self):
        workers=[]
        class ControlledPreview(QThread):
            ready=Signal(QImage)
            def __init__(self,path,start,parent,width=1920,height=1080,aspect_ratio_mode='fit'):
                super().__init__(parent)
                self.path,self.start_seconds=path,start
                self.width,self.height,self.aspect_mode=width,height,aspect_ratio_mode
                self.release=threading.Event()
                workers.append(self)
            def stop(self):
                self.requestInterruption()  # Completion is intentionally delayed.
            def run(self):
                self.release.wait(10)
        return workers,ControlledPreview

    @staticmethod
    def release_workers(workers):
        for worker in workers:
            try:
                worker.release.set();worker.wait(1000)
            except RuntimeError:
                pass

    def test_changes_wait_for_old_worker_and_coalesce_to_latest_settings(self):
        self.prepare()
        workers,worker_class=self.controlled_preview()
        try:
            with patch('ui.dashboard.PreviewWorker',worker_class):
                self.flow._request_preview()
                old=workers[0]
                self.win.output_panel.set_aspect_mode('crop')
                self.await_preparation();self.flow._request_preview()
                self.win.output_panel.set_aspect_mode('fit')
                self.await_preparation();self.flow._request_preview()
                self.assertEqual(len(workers),1)
                self.assertIs(self.flow.preview_worker,old)
                self.assertTrue(self.flow.background_running())
                self.assertTrue(old.isInterruptionRequested())
                old.release.set()
                self.await_condition(lambda: len(workers)==2)
                self.assertEqual(workers[-1].aspect_mode,'fit')
                self.assertTrue(self.flow.background_running())
                workers[-1].release.set()
                self.await_condition(lambda: self.flow.preview_worker is None)
                self.assertFalse(self.flow.background_running())
        finally:
            self.release_workers(workers)

    def test_close_waits_for_stopping_preview_without_starting_pending_work(self):
        self.prepare();self.win.show()
        workers,worker_class=self.controlled_preview()
        try:
            with patch('ui.dashboard.PreviewWorker',worker_class):
                self.flow._request_preview()
                self.win.output_panel.set_aspect_mode('crop')
                self.await_preparation();self.flow._request_preview()
                self.win.close()
                self.assertTrue(self.win.isVisible())
                self.assertTrue(self.flow.background_running())
                workers[0].release.set()
                self.await_condition(lambda: not self.win.isVisible())
                self.assertEqual(len(workers),1)
                self.assertIsNone(self.flow.preview_worker)
        finally:
            self.release_workers(workers)
