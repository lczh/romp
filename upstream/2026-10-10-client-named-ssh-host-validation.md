---
title: Validate every client-named ssh host with the attach route's existing guard and keep it positional
status: candidate
where: kernel/kernel.py (_open_folder_remote, _update_remote, _pull_remote and _start_remote run the client-named host through _safe_ssh_host, the gate the attach route already applies, and the folder-open ssh argvs keep the host positional); tests/test_kernel_open_terminal.py, tests/test_kernel_remote_update.py, tests/test_kernel_remote_pull.py and tests/test_kernel_remote_start.py (a host the guard refuses is rejected before any ssh or git); upstream/2026-10-10-client-named-ssh-host-validation.md (this entry)
added: 2026-10-10
pr:
tier: fix
offered:
closed:
---
The remote folder-open handler and the /tunnels start, update and pull routes derived their ssh host from a client-supplied value without the validation the attach route already applies (_safe_ssh_host). The same host rule now runs in _open_folder_remote, _update_remote, _pull_remote and _start_remote, so every client-named host is checked the one way, and the folder-open ssh argvs keep the host positional like the tunnel ssh argvs already do. Each sink gets a test that drives a host the guard refuses through it: it reaches ssh or git before the change and is rejected before any ssh or git runs after.
