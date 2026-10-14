from app.core.queue import FolderWatcher


def test_recursive_stability_and_path_size_mtime_fingerprint(tmp_path,monkeypatch):
    import app.core.queue as module
    a=tmp_path/'camera_a'/'same.mov';a.parent.mkdir();a.write_bytes(b'a')
    b=tmp_path/'camera_b'/'same.mov';b.parent.mkdir();b.write_bytes(b'b')
    calls=[]
    class ClockEvent:
        count=0
        def is_set(self):
            return self.count>=6
        def wait(self,seconds):
            self.count+=1
            if self.count==3:
                a.write_bytes(b'new version')
            return self.is_set()
    event=ClockEvent()
    monkeypatch.setattr(module.time,'monotonic',lambda:event.count*3.)
    watcher=FolderWatcher(tmp_path,'_TV',lambda paths:calls.append(paths))
    watcher.stop_event=event
    watcher.run()
    assert len(calls)==2
    assert set(calls[0])=={a,b}
    assert calls[1]==[a]
