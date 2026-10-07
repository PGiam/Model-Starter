import itertools
import os
import urllib.error

from app import persist, store


class FakeGitHub:
    """Just enough of the Git Data API to commit files and read them back."""

    def __init__(self):
        self.objs, self.refs, self.ids, self.private = {}, {}, itertools.count(), True

    def _new(self, obj):
        sha = f"sha{next(self.ids)}"
        self.objs[sha] = obj
        return sha

    def __call__(self, method, path, body=None):
        if path == "":
            return {"private": self.private}
        if path.startswith("/git/ref/heads/") and method == "GET":
            ref = path.split("/git/ref/")[1]
            if ref not in self.refs:
                raise urllib.error.HTTPError(path, 404, "nf", {}, None)
            return {"object": {"sha": self.refs[ref]}}
        if path == "/git/blobs":
            return {"sha": self._new({"content": body["content"]})}
        if path.startswith("/git/blobs/"):
            return self.objs[path.rsplit("/", 1)[1]]
        if path == "/git/trees":
            files = dict(self.objs[body["base_tree"]]["files"]) if body.get("base_tree") else {}
            files.update({e["path"]: e["sha"] for e in body["tree"]})
            return {"sha": self._new({"files": files})}
        if path.startswith("/git/trees/"):
            files = self.objs[path.split("/git/trees/")[1].split("?")[0]]["files"]
            return {"tree": [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]}
        if path == "/git/commits":
            return {"sha": self._new({"tree": body["tree"]})}
        if path.startswith("/git/commits/"):
            return {"tree": {"sha": self.objs[path.rsplit("/", 1)[1]]["tree"]}}
        if path == "/git/refs":
            self.refs[body["ref"].removeprefix("refs/")] = body["sha"]
            return {}
        if path.startswith("/git/refs/heads/") and method == "PATCH":
            self.refs[path.split("/git/refs/")[1]] = body["sha"]
            return {}
        raise AssertionError(f"unexpected {method} {path}")


def test_round_trip(tmp_path, monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(persist, "_api", gh)
    monkeypatch.setattr(persist, "REPO", "o/r")
    monkeypatch.setattr(persist, "TOKEN", "t")
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "a"))
    store.write_playbook("# Rules\n- one", "Phil", "test")
    sid = "c" * 16
    d = store.session_dir(sid)
    os.makedirs(os.path.join(d, "files"))
    for name, data in (("meta.json", b"{}"), ("files/X_Model_v1.xlsx", b"xl"), ("files/10k.htm", b"big")):
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
    persist._pending.clear()
    persist._commit(["playbook/current.md", "playbook/history.json", "playbook/v1.md"])
    persist._commit([f"sessions/{sid}/meta.json", f"sessions/{sid}/files/X_Model_v1.xlsx"])
    assert not persist._kept(f"sessions/{sid}/files/10k.htm")

    # A fresh, empty disk (what a free host has after it sleeps) gets everything back.
    monkeypatch.setattr(store, "DATA_DIR", str(tmp_path / "b"))
    persist.restore()
    assert persist.status["error"] is None
    assert store.read_playbook() == "# Rules\n- one"
    assert store.playbook_history()[-1]["author"] == "Phil"
    with open(os.path.join(store.session_dir(sid), "files", "X_Model_v1.xlsx"), "rb") as f:
        assert f.read() == b"xl"


def test_refuses_public_data_repo(tmp_path, monkeypatch):
    gh = FakeGitHub()
    gh.private = False
    monkeypatch.setattr(persist, "_api", gh)
    monkeypatch.setattr(persist, "REPO", "o/r")
    monkeypatch.setattr(persist, "TOKEN", "t")
    monkeypatch.setattr(persist, "status", {"enabled": True})
    persist.restore()
    assert not persist.enabled() and "public" in persist.status["error"]
