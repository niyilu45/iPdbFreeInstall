"""Compare native execution and continue-to-breakpoint tracing."""
import io
import time
import inspect
import statistics
import portable_ipdb
from IPython.core.debugger import Pdb
import bdb


def Workload():
    total = 0
    for number in range(100000):
        total += number
    return total


class Runner(Pdb):
    def user_line(self, frame):
        self.set_continue()


def Measure():
    start = time.perf_counter()
    Workload()
    native = time.perf_counter() - start
    results = {}
    optimized = Pdb.stop_here
    # Recover the upstream method captured by the small wrapper.
    original = optimized.__closure__[0].cell_contents
    try:
        for label, method in [('upstream', original), ('optimized', optimized)]:
            Pdb.stop_here = method
            timings = []
            for unused in range(3):
                debugger = Runner(stdout=io.StringIO())
                debugger.set_break(__file__, inspect.getsourcelines(Workload)[1] + 4)
                start = time.perf_counter()
                debugger.runcall(Workload)
                timings.append(time.perf_counter() - start)
                debugger.clear_all_breaks()
            results[label] = statistics.median(timings)
    finally:
        Pdb.stop_here = optimized
    print('native={:.4f}s upstream={:.4f}s optimized={:.4f}s speedup={:.2f}x'.format(
        native, results['upstream'], results['optimized'], results['upstream'] / results['optimized']))


if __name__ == '__main__':
    Measure()
