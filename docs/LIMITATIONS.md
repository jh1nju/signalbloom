# Known limitations

- If registration succeeds but the reply is lost, the setup code is used up and the user needs a new one. A production version would allow a short retry window on the server.
- Devices that never answer ping (for example firewalled PCs) rely on the ARP table, so they can take several minutes to show as offline.
