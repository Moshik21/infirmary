"""A do-nothing adapter for the fixtures: the install and the loader only have to find it and construct it. It offers no checks and no alarms."""


class Source(object):
    def __init__(self, cfg):
        self.cfg = cfg
