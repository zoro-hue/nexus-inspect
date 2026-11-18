class Recorder:
    def save_session(self, app, session, response):
        pass


def test_recorder():
    assert Recorder().save_session(None, None, None) is None
