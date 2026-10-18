# Lane: performance

Hot paths, memory, reads and queues. Ask what this code costs at ten times today's data and traffic. Report a cost
the diff adds, with the path that makes it real, not a micro-optimisation.

## PERF-01 No query or call inside a loop

A loop over records does not run one query, network call or model call per item when one batched call would do (the
N+1 pattern).
**Default:** important; blocker on a path every request takes.

## PERF-02 Lists are paginated and bounded

An endpoint, query or export that returns many rows has a limit and a page or cursor. Nothing loads a whole table.
**Default:** important; blocker when the table is large or grows without bound.

## PERF-03 Memory stays bounded

Large files, results and streams are processed in chunks or streamed, not read whole into memory. Caches and maps
that grow have a size limit or an expiry.
**Default:** important.

## PERF-04 Slow work leaves the request path

Work that takes seconds (exports, emails, model calls, image processing, third-party sync) runs in a background job
or queue, and the request returns quickly.
**Default:** important.

## PERF-05 Jobs and queues are safe to retry

A job is idempotent, has a timeout and a retry limit, and does not flood a downstream service when many run at once.
**Default:** important.

## PERF-06 Caches are correct before they are fast

A new cache has a clear key (including the user or tenant when the data is private), an expiry, and a way to be
cleared when the source changes.
**Default:** blocker when a cache can serve one user's data to another; important otherwise.

## PERF-07 The client stays light

A client change does not add a large dependency, a large image or a blocking script to the first load without need.
Heavy parts load on demand.
**Default:** note; important for a clear regression of load time.

## PERF-08 Expensive work is not repeated

The same costly computation, parse or fetch is not done several times in one request or render when it could be done
once.
**Default:** note.

## PERF-09 Timeouts exist on every outbound call

A call to a database, a service or the network has a timeout, so one slow dependency cannot hold every worker.
**Default:** important.
