# Known limitations

- If registration succeeds but the reply is lost, the setup code is used up and the user needs a new one. A production version would allow a short retry window on the server.
- Devices that never answer ping (for example firewalled PCs) rely on the ARP table, so they can take several minutes to show as offline.
- The device list sent with readings is empty until the first full scan finishes (up to 30 seconds after start).
- If the speed test is still running when the next ping starts, that reading's latency may be raised. This only happens on links slow enough that the download takes more than about 25 seconds, and it is noted in the log.
- After registering again, readings still in the buffer are sent under the new site.
- If the cloud rejects the device list (400), the agent finds this by retrying a single reading without devices. It saves the device list to `rejected.jsonl` and sends readings without devices until the next full scan (up to `device_scan_seconds`), so the dashboard's device list doesn't update in that time. If the next scan's list is also rejected, the same thing happens again.
- A fix that arrives before the first full device scan finishes (up to 30 seconds after start) is reported as failed, because the target device isn't in the list yet.
- If the agent stops in the middle of a fix (the 2-second simulated wait), it doesn't run it again on restart. It reports it as failed.
- If `data_dir/commands.json` is deleted or unreadable, the agent forgets which fixes it has handled. The cloud only sends each fix once, so this matters only if the cloud sends one again.
