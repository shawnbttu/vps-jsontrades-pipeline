Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -ExecutionPolicy Bypass -File ""C:\VScode\tools\pull_vps_apolloes_hermes_json.ps1"" -VpsHost 104.245.104.71 -KeyPath ""C:\Users\tanve\.ssh\id_ed25519_vps_nt""", 0, False
