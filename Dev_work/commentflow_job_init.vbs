Option Explicit
' Launch Windows PowerShell directly (no CMD window flash)
Dim sh, cmd
Set sh = CreateObject("WScript.Shell")
cmd = "powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -File ""D:\workspaces\github\onebabo\CommentFlow\Dev_work\commentflow_job_init.ps1"""
sh.Run cmd, 1, False
