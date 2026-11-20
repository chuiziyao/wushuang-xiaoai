"""加密层测试。"""

from wushuang.crypto import SecretBox


def test_roundtrip(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    token = box.encrypt("小米密码123！")
    assert token and token != "小米密码123！"
    assert box.decrypt(token) == "小米密码123！"


def test_key_persistence(tmp_path):
    key_path = tmp_path / "secret.key"
    box1 = SecretBox(key_path)
    token = box1.encrypt("cookie=MUSIC_U=abc")
    box2 = SecretBox(key_path)
    assert box2.decrypt(token) == "cookie=MUSIC_U=abc"


def test_empty_and_tampered(tmp_path):
    box = SecretBox(tmp_path / "secret.key")
    assert box.encrypt("") == ""
    assert box.encrypt(None) == ""
    assert box.decrypt("") == ""
    assert box.decrypt(None) == ""
    assert box.decrypt("not-a-valid-token") == ""
