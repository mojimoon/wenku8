Option Explicit

Dim fso, shell, today, recordFile, recordPath, lastDate, file, exec
Dim pythonScript, gitAdd, gitCommit, gitPush, cmd, result

' 设置路径
recordPath = "D:\dev\epub8\last_success_date.txt"
pythonScript = "python D:\dev\epub8\main.py"
gitAdd = "git -C D:\dev\epub8 add ."
gitPush = "git -C D:\dev\epub8 push origin main"

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
today = Year(Now) & "-" & Right("0" & Month(Now),2) & "-" & Right("0" & Day(Now),2)
gitCommit = "git -C D:\dev\epub8 commit -m ""auto update " & today & """"

' 检查当天是否已执行
If fso.FileExists(recordPath) Then
    Set file = fso.OpenTextFile(recordPath, 1)
    lastDate = Trim(file.ReadAll)
    file.Close
    If lastDate = today Then
        WScript.Quit
    End If
End If

' 执行 main.py（隐藏窗口，0=隐藏窗口，True=不等待）
result = shell.Run(pythonScript, 0, True)
If result <> 0 Then
    WScript.Quit
End If

' git add
shell.Run gitAdd, 0, True
' git commit（如果有更改才会提交，否则忽略错误）
shell.Run gitCommit, 0, True
' git push
result = shell.Run(gitPush, 0, True)
If result <> 0 Then
    WScript.Quit
End If

' 写入执行日期
Set file = fso.OpenTextFile(recordPath, 2, True)
file.Write today
file.Close