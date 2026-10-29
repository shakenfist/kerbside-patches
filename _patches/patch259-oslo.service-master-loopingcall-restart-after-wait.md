# patch259: loopingcall: Allow a restart once wait() returns

- **Status:** draft
- **Change:** oslo.service, Change-Id `I748102bbeab585f8ceb0eb3729451753866b486b`
- **Post:** as the first comment on the change once it is uploaded, after
  filling in the bug link.

Everything below the line is the comment.

---

**How this was found.** Neutron change
[1012625](https://review.opendev.org/c/openstack/neutron/+/1012625) shortens
CPython's thread switch interval to 10 µs in neutron's tests. That lets races
in short pure-Python sections show up in CI rather than only under load. Its
py314 job then
[failed](https://zuul.opendev.org/t/openstack/build/ff5f5ca670414e52992680b6aa552054)
`test_periodic_worker_lifecycle_contract` with `RuntimeError: A fixed interval
looping call can only run one function at a time`. The error was raised by
`start()` right after `stop()` and `wait()` on the same
`FixedIntervalLoopingCall`.

**The window.** In the threading backend, the loop thread signals done at
the end of `_run_loop`, and that wakes `wait()`. `_thread` is only cleared
later, by the futurist done callback `_on_done`, after the loop function has
returned. A `start()` made in between sees `_thread` still set and raises.

The eventlet backend has no such window. Its `GreenThread.link` callbacks
run in the finishing greenthread before any waiter is scheduled. So this
arrived with the threading backend
([5de514f](https://opendev.org/openstack/oslo.service/commit/5de514f1f851412b18793c88289f7d2b01e5f217),
4.2.0).

**Evidence.**

| Run | Without the fix | With the fix |
|-----|-----------------|--------------|
| Standalone script, `_on_done` delayed by 10 ms, stop / wait / start repeated | 200 of 200 restarts fail | 0 of 200 |
| Same script, no delay, 10 µs switch interval, four CPU-bound threads | 4 of 2000 | 0 of 2000 |

On neutron master with the 10 µs interval, `neutron.tests.unit.test_worker`
failed 4 of 160 runs. At the default 5 ms it failed 0 of 160.

**Design notes.**

- `_is_looping()` requires both a thread and a done event that has not been
  signalled. Checking the done event alone would change one case. A loop
  whose greenthread is killed never signals done, yet today `_on_done` still
  clears `_thread`, so the loop can be restarted. Requiring the thread keeps
  that behaviour.
- `start()` no longer waits for `_thread` to clear, so an old loop's done
  callback can now run after a restart. `_on_done` therefore clears only the
  thread it was registered for. The fourth new test covers this.
- The tests hold back `_on_done` with a mock instead of relying on timing,
  so they are deterministic.
- `FutureEvent` gains `ready()`, matching eventlet's `Event.ready()`, which
  the common code now calls.

**Not in this change.** The abandoned
[997384](https://review.opendev.org/c/openstack/oslo.service/+/997384) fixed
a separate threading-backend problem. `_spawn_loop` makes a thread pool for
each `start()` and never shuts it down, so it leaks one thread per start.
That is unrelated to this race and is left alone here.

**Neutron.** Neutron's `PeriodicWorker.reset()` calls stop, wait and start on
one looping call. Production neutron does not reach it, because cotyledon
restarts the worker process on SIGHUP instead. Its unit tests do reach it, so
neutron carries a stopgap that builds a fresh looping call after `wait()`
(Change-Id `I9043501d8cda2c422e873e81d68ccd030e8549b1`). That stopgap stays
until a release with this fix reaches upper-constraints. Any other consumer
that restarts a looping call right after `wait()` can hit the same error.

Bug: (link to be added once filed)
