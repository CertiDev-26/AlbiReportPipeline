$action = New-ScheduledTaskAction `
    -Execute "C:\Python314\python.exe" `
    -Argument "C:\Users\jackson\Desktop\projects\AlbiReportPipeline\automate.py" `
    -WorkingDirectory "C:\Users\jackson\Desktop\projects\AlbiReportPipeline"

$trigger = New-ScheduledTaskTrigger -Daily -At "03:00AM"

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

Register-ScheduledTask `
    -TaskName "AlbiDailyReportDeploy" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host "Task 'AlbiDailyReportDeploy' registered successfully." -ForegroundColor Green
