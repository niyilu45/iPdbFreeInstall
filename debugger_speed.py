"""Avoid IPython frame inspection while continuing to breakpoints."""


def Install():
    from IPython.core.debugger import Pdb
    if getattr(Pdb, '_portable_speed', False):
        return
    Pdb._portable_speed = True
    original = Pdb.stop_here
    original_continue = Pdb.set_continue

    def Continue(self):
        import bdb
        enabled = any(entry.enabled
                      for filename, lines in self.breaks.items()
                      for line in lines
                      for entry in bdb.Breakpoint.bplist.get((filename, line), ()))
        if enabled or not self.breaks:
            return original_continue(self)
        # Native bdb only removes tracing when its breakpoint map is empty.
        # Keep disabled entries available for the next interactive stop.
        saved = self.breaks
        self.breaks = {}
        try:
            return original_continue(self)
        finally:
            self.breaks = saved

    def StopHere(self, frame):
        # Bdb cannot stop by stepping in continue mode. Breakpoint evaluation
        # still happens in dispatch_line/break_here, including conditions,
        # ignore counts and temporary breakpoints. Keep all stepping paths.
        if self.stopframe is self.botframe and self.stopframe is not None and self.stoplineno == -1:
            return False
        return original(self, frame)

    Pdb.stop_here = StopHere
    Pdb.set_continue = Continue
