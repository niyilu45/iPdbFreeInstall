"""Avoid IPython frame inspection while continuing to breakpoints."""


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_speed', False):
        return
    Pdb._portable_speed = True
    original = Pdb.stop_here

    def StopHere(self, frame):
        # Bdb cannot stop by stepping in continue mode. Breakpoint evaluation
        # still happens in dispatch_line/break_here, including conditions,
        # ignore counts and temporary breakpoints. Keep all stepping paths.
        if self.stopframe is self.botframe and self.stopframe is not None and self.stoplineno == -1:
            return False
        return original(self, frame)

    Pdb.stop_here = StopHere
