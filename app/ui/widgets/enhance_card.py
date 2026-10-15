"""Reusable enhancement controls and asynchronous preview, for Fixer / future Converter."""
import threading
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QFormLayout, QSlider, QDoubleSpinBox,
                              QComboBox, QLineEdit, QFileDialog)
from app.core.enhance.model import EnhanceSettings, settings_from, HONEST_TOOLTIP, LEVELS
from app.core.enhance.planning import filter_evidence
from app.core.enhance.preview import render_preview
from app.core.ffprobe import probe
from app.ui.widgets.common import Card, Pills, Toggle, label, button, row
from app.ui.widgets.split_preview import SplitPreview
from app.ui import dialogs


class EnhanceCard(Card):
    preview_ready = Signal(object)
    preview_failed = Signal(str)
    changed = Signal()

    def __init__(self, value=None, get_options=None, manager=None):
        super().__init__('Enhance Quality', True)
        self.get_options, self.manager = get_options, manager
        self.caps = {}
        self.cancel = threading.Event()
        self.busy = False
        self.preview_thread = None
        self.last_result = None
        try:
            settings = settings_from(value)
            warning = ''
        except (TypeError, ValueError) as exc:
            settings, warning = EnhanceSettings(), 'Invalid saved enhancement settings reset to Off: ' + str(exc)
        self.pills = Pills(LEVELS, settings.level)
        self.pills.buttons['Balanced'].setText('Balanced ★')
        self.pills.setToolTip(HONEST_TOOLTIP)
        self.box.addWidget(self.pills)
        self.box.addWidget(label(HONEST_TOOLTIP, 'muted'))
        self.notes = label(warning or 'Off: no enhancement filters or quality-budget changes. Source files are never edited.', 'muted')
        self.box.addWidget(self.notes)
        self.custom = QWidget(); form = QFormLayout(self.custom)
        self.values, self.rows = {}, {}
        for title, key, minimum, maximum, step, help_text in [
            ('Noise reduction', 'denoise', 0,4,.1, '0 disables denoising. Higher values remove noise but can soften real detail.'),
            ('Debanding', 'deband', 0,.04,.001, 'Small values smooth gradient bands. 0 disables it.'),
            ('Luma sharpening', 'sharpen', 0,.6,.05, 'Small luma-only amounts avoid chroma halos. This cannot recover missing detail.'),
            ('Gamma', 'gamma', 1,1.02,.001, 'A gentle mid-tone adjustment. Check highlights/shadows in preview; this is not automatic recovery.'),
            ('Contrast', 'contrast', .98,1,.001, 'Only small contrast reduction is offered. Preview highlights/shadows; clipping is not detected or repaired automatically.'),
            ('Saturation', 'saturation', .95,1,.01, 'Conservative saturation; not an automatic color-restoration claim.'),
        ]:
            widget = QWidget(); layout = row(); widget.setLayout(layout)
            slider = QSlider(Qt.Horizontal); scale = round(1/step)
            slider.setRange(round(minimum*scale), round(maximum*scale)); slider.setValue(round(getattr(settings,key)*scale))
            spin = QDoubleSpinBox(); spin.setDecimals(3); spin.setRange(minimum,maximum); spin.setSingleStep(step); spin.setValue(getattr(settings,key))
            slider.valueChanged.connect(lambda v,s=spin,n=scale:s.setValue(v/n))
            spin.valueChanged.connect(lambda v,s=slider,n=scale:s.setValue(round(v*n)))
            spin.valueChanged.connect(self.option_changed)
            layout.addWidget(slider,1); layout.addWidget(spin)
            widget.setToolTip(help_text)
            form.addRow(title,widget); self.values[key]=spin; self.rows[key]=widget
        self.method = self.combo(['Auto','hqdn3d','nlmeans'],settings.denoiser)
        self.method.setToolTip('Auto adapts to measured speed and Safe mode. nlmeans is skipped when too slow, with a visible explanation.')
        form.addRow('Denoiser',self.method)
        self.tenbit = Toggle('10-bit CPU processing + dithering (TV encode stays 8-bit)', settings.processing_10bit)
        form.addRow('',self.tenbit)
        self.color = self.combo(['Auto','bt601','bt709','bt2020'],settings.color)
        self.color.setToolTip('Convert color, not just tags. TV output requires the matrix appropriate to its resolution; mismatches are explained.')
        form.addRow('Target color matrix',self.color)
        self.range = self.combo(['Auto','limited','full'],settings.input_range)
        self.range.setToolTip('Override only if the source range tags are wrong. Incorrect overrides can wash out or crush the picture.')
        form.addRow('Source range override',self.range)
        self.scaler = self.combo(['lanczos','spline','bicubic'],settings.scaler)
        form.addRow('Software scaler',self.scaler)
        self.box.addWidget(self.custom)
        self.extra = QWidget(); extra = QFormLayout(self.extra)
        self.upscale = self.combo(['Keep','1080p','4K'],settings.upscale)
        self.upscale.setToolTip('Explicit interpolation, not AI. Never changes FPS. Select a TV preset/cap that allows the requested dimensions.')
        extra.addRow('Upscale to…',self.upscale)
        self.loudnorm = Toggle('Audio: EBU R128 loudness target (one pass)',settings.loudnorm)
        self.cleanup = Toggle('Audio: gentle high/low-pass + noise reduction',settings.audio_cleanup)
        extra.addRow('',self.loudnorm);extra.addRow('',self.cleanup)
        self.ai = Toggle('Opt-in external AI upscaler · user-provided ncnn CLI',settings.ai_enabled)
        self.ai.setToolTip('Runs your executable on temporary PNG frames. No model is bundled or downloaded. May invent detail. Requires explicit upscale, verified progressive CFR 8-bit SDR and ample disk space.')
        extra.addRow('',self.ai)
        self.ai_path = QLineEdit(settings.ai_executable)
        extra.addRow('External executable',self.ai_path)
        extra.addRow('',button('Choose external executable',self.browse_ai))
        self.ai_scale = self.combo(['2','4'],str(settings.ai_scale));extra.addRow('External AI scale',self.ai_scale)
        self.box.addWidget(self.extra)
        for combo in (self.method,self.color,self.range,self.scaler):
            combo.currentIndexChanged.connect(self.option_changed)
        self.tenbit.toggled.connect(self.option_changed)
        for widget in (self.loudnorm,self.cleanup,self.ai):
            widget.toggled.connect(lambda *_:self.changed.emit())
        for combo in (self.upscale,self.ai_scale):
            combo.currentIndexChanged.connect(lambda *_:self.changed.emit())
        self.ai_path.textChanged.connect(lambda *_:self.changed.emit())
        preview_form = QFormLayout()
        self.source = QLineEdit();self.source.setPlaceholderText('Choose a local source file for preview')
        preview_form.addRow('Preview source',self.source)
        self.timestamp = QDoubleSpinBox();self.timestamp.setDecimals(3);self.timestamp.setRange(0,1_000_000);self.timestamp.setSuffix(' s')
        preview_form.addRow('Preview timestamp',self.timestamp)
        self.box.addLayout(preview_form)
        self.render_button = button('Preview before / after',self.start_preview,True)
        self.clip_button = button('Render 3-second preview clip…',self.start_clip)
        self.stop_button = button('Cancel preview',self.cancel.set);self.stop_button.setEnabled(False)
        self.box.addLayout(row(button('Choose preview file',self.browse_source),self.render_button,self.clip_button,self.stop_button))
        self.comparison = SplitPreview();self.box.addWidget(self.comparison)
        self.box.addWidget(button('Preview details / exact filter chain',self.show_details))
        self.preview_ready.connect(self.finished)
        self.preview_failed.connect(self.failed)
        self.pills.selected.connect(self.level_changed)
        self.level_changed(settings.level)
        if manager:
            # No previews can consume a GPU session alongside the queue or startup scan.
            manager.started.connect(self.cancel.set)

    @staticmethod
    def combo(items,value):
        combo=QComboBox();combo.addItems(items);combo.setCurrentText(value);return combo

    def option_changed(self,*_):
        if self.pills.value != 'Custom':
            self.pills.choose('Custom')
        self.changed.emit()

    def level_changed(self,level):
        self.custom.setVisible(level=='Custom')
        self.extra.setEnabled(level!='Off')
        if level=='Off':
            self.notes.setText('Off: no enhancement filters / headroom changes for SDR. HDR→TV requires explicit conversion.')
        else:
            self.notes.setText(f'{level}: quality budget + capability-gated filters. Preview reports the actual chain and rough speed estimate; TV safety takes priority.')
        self.changed.emit()

    def settings(self):
        return EnhanceSettings(level=self.pills.value,**{k:w.value() for k,w in self.values.items()},
            denoiser=self.method.currentText(),processing_10bit=self.tenbit.isChecked(),color=self.color.currentText(),
            input_range=self.range.currentText(),upscale=self.upscale.currentText(),scaler=self.scaler.currentText(),
            loudnorm=self.loudnorm.isChecked(),audio_cleanup=self.cleanup.isChecked(),ai_enabled=self.ai.isChecked(),
            ai_executable=self.ai_path.text().strip(),ai_scale=int(self.ai_scale.currentText())).to_dict()

    def set_capabilities(self,caps):
        self.caps=caps or {}
        proof=filter_evidence(self.caps)
        for level in LEVELS:
            if level != 'Off':
                self.pills.buttons[level].setEnabled(bool(proof.get('color_tags',{}).get('available')))
                self.pills.buttons[level].setToolTip(proof.get('color_tags',{}).get('reason') or HONEST_TOOLTIP)
        mapping={'denoise':'hqdn3d','deband':'deband','sharpen':'unsharp','gamma':'eq','contrast':'eq','saturation':'eq'}
        for key,feature in mapping.items():
            available=proof.get(feature,{}).get('available',False)
            self.rows[key].setEnabled(available)
            if not available:
                self.rows[key].setToolTip(proof.get(feature,{}).get('reason','No successful filter probe; re-scan hardware'))
        for method in ('hqdn3d','nlmeans'):
            index=self.method.findText(method);item=self.method.model().item(index)
            item.setEnabled(bool(proof.get(method,{}).get('available')))
            self.method.setItemData(index,proof.get(method,{}).get('reason','Unproven'),Qt.ToolTipRole)
        self.tenbit.setEnabled(bool(proof.get('dither',{}).get('available')))
        self.tenbit.setToolTip(proof.get('dither',{}).get('reason') or 'Proven CPU 10-bit / error-diffusion dither; not 10-bit TV encoding')
        self.loudnorm.setEnabled(bool(proof.get('loudnorm',{}).get('available')))
        self.loudnorm.setToolTip(proof.get('loudnorm',{}).get('reason') or 'One-pass EBU R128 target; not a measured two-pass guarantee')
        self.cleanup.setEnabled(all(proof.get(k,{}).get('available') for k in ('highpass','lowpass','afftdn')))
        self.cleanup.setToolTip('; '.join(proof.get(k,{}).get('reason','No runtime proof') for k in ('highpass','lowpass','afftdn') if not proof.get(k,{}).get('available')) or 'Proven highpass / lowpass / afftdn; optional light cleanup')
        for i in range(self.scaler.count()):
            p=proof.get('scale_'+self.scaler.itemText(i),{})
            self.scaler.model().item(i).setEnabled(bool(p.get('available')))
            self.scaler.setItemData(i,p.get('reason','Unproven'),Qt.ToolTipRole)
        for combo in (self.color,self.range):
            for index in range(combo.count()):
                value=combo.itemText(index)
                available=value=='Auto' or bool(proof.get('color_convert',{}).get('available'))
                if combo is self.color and value=='bt2020':available=False
                combo.model().item(index).setEnabled(available)
                combo.setItemData(index,'' if available else 'Unproven color conversion / BT.2020 is not an SDR TV target in this workflow',Qt.ToolTipRole)
            combo.setToolTip('Color conversion requires a successful zscale test. HDR requires float tonemap; failed prerequisites block instead of silently retagging.')
        self.notes.setText('Enhancement capability tests loaded. Unproven controls are greyed; exact whole-chain preflight still runs before encoding.')

    def browse_source(self):
        path=QFileDialog.getOpenFileName(self,'Preview local media','','Media files (*.*)')[0]
        if path:self.source.setText(path)

    def browse_ai(self):
        path=QFileDialog.getOpenFileName(self,'User-provided ncnn-compatible executable','','Executables (*.exe);;All files (*)')[0]
        if path:self.ai_path.setText(path)

    def start_clip(self):
        path=dialogs.save_file(self,'enhanced-preview.mp4','MP4 (*.mp4)')
        if path:self.start_preview(path)

    def start_preview(self,clip_output=None):
        if isinstance(clip_output,bool):clip_output=None
        if self.busy:return
        if self.manager and (self.manager.running or self.manager.analysis_running or self.manager.hardware_scanning):
            self.failed('Finish/stop the queue and hardware scan before previewing; preview never overbooks GPU sessions.')
            return
        source=Path(self.source.text().strip())
        if not source.is_file():
            self.failed('Choose an existing local media file for preview.');return
        try:
            options=self.get_options() if self.get_options else None
        except Exception as exc:
            self.failed(str(exc));return
        if options is None:
            self.failed('No encoding options provider is attached.');return
        options=replace(options,enhance=self.settings())
        if options.hardware=='Global' and self.manager:options.hardware=self.manager.global_hardware
        if options.quality_speed=='Global' and self.manager:options.quality_speed=self.manager.quality_speed
        lock = self.manager.preview_lock if self.manager else None
        if lock and not lock.acquire(blocking=False):
            self.failed('Another enhancement preview is still active. Cancel it or wait.');return
        self.busy=True;self.cancel.clear()
        self.render_button.setEnabled(False);self.clip_button.setEnabled(False);self.stop_button.setEnabled(True)
        self.notes.setText('Rendering in background… Cancel is available. No unannounced GPU/CPU fallback.')
        timestamp=self.timestamp.value();caps=self.caps
        def emit(name, value):
            try:
                getattr(self,name).emit(value)
            except RuntimeError:
                pass  # Page was disposed/rebuilt while cancellation was in flight.
        def work():
            try:
                media=probe(source,cancel=self.cancel)
                result=render_preview(media,options,caps,timestamp,self.cancel,clip_output)
                if not self.cancel.is_set():emit('preview_ready',result)
                else:emit('preview_failed','Preview cancelled')
            except Exception as exc:
                emit('preview_failed','Preview cancelled' if self.cancel.is_set() else str(exc))
            finally:
                if lock:lock.release()
        self.preview_thread=threading.Thread(target=work,daemon=True);self.preview_thread.start()

    def dispose(self):
        self.cancel.set()

    def reset_busy(self):
        self.busy=False;self.render_button.setEnabled(True);self.clip_button.setEnabled(True);self.stop_button.setEnabled(False)

    def finished(self,result):
        self.reset_busy();self.last_result=result
        self.comparison.set_images(result.before,result.after)
        speed=result.plan.enhancement.get('estimated_speed')
        estimate=f'{speed:.2f}× (rough synthetic estimate)' if speed is not None else 'unknown (not measured)'
        self.notes.setText(result.plan.encoding_status+f'\nEstimated speed: {estimate}. Preview rendered in {result.elapsed:.2f}s.'+
                           ('\nClip saved: '+result.clip if result.clip else ''))
        if self.manager:
            self.manager.log.emit('INFO','Enhancement preview: '+result.plan.encoding_status,str(result.plan.command[-1]),
                                  '\n'.join(map(str,result.commands)))

    def failed(self,message):
        self.reset_busy();self.notes.setText('Preview: '+message)
        if self.manager:self.manager.log.emit('WARN','Enhancement preview: '+message,'','')

    def show_details(self):
        if not self.last_result:
            dialogs.details(self,'Enhancement preview','Render a preview to inspect its exact commands, applied filters and capability notes.')
            return
        import json
        dialogs.details(self,'Enhancement preview',json.dumps(self.last_result.plan.enhancement,ensure_ascii=False,indent=2)+
                        '\n\nCommands:\n'+'\n'.join(map(str,self.last_result.commands)))
