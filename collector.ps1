# 电池遥测采集器 —— 常驻进程，逐行输出 JSON
# 由 battery_server.py 通过 stdin 传入采样间隔(秒)，stdout 输出 JSONL
$ErrorActionPreference = 'SilentlyContinue'

# 强制 ASCII 输出：Python 端按 utf-8 解码管道，避免中文乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::ASCII } catch { }

$interval = 0.5
$line = [Console]::In.ReadLine()
if ($line) { $interval = [double]::Parse($line.Trim()) }
if ($interval -le 0) { $interval = 0.5 }

# 缓存 Win32_Battery（静态信息变化很少），避免每次都跑重量级查询
$wbCache = $null
$wbStamp = [datetime]::MinValue

function Get-Win32Battery {
    if ($wbCache -and ((Get-Date) - $wbStamp).TotalSeconds -lt 5) {
        return $wbCache
    }
    $script:wbCache = Get-CimInstance Win32_Battery
    $script:wbStamp = Get-Date
    return $script:wbCache
}

# 仅输出 ASCII 状态码，中文文案由 Python 端映射，避免管道编码问题
$acCodeMap = @{
    1 = 'ON_BATTERY'; 2 = 'AC_CONNECTED'; 3 = 'DISCHARGING'; 4 = 'LOW'; 5 = 'SAVER'
    6 = 'CHARGING'; 7 = 'ON_BATTERY_CHARGING'; 8 = 'CHARGING_REDUCED'; 9 = 'ON_BATTERY_HIGH'
    11 = 'PARTIAL_CHARGE'; 13 = 'FULLY_CHARGED'
}

while ($true) {
    try {
        $bs = Get-CimInstance -Namespace root\WMI -ClassName BatteryStatus
        $wb = Get-Win32Battery

        $chargeW = 0.0; $disW = 0.0; $voltV = 0.0; $amp = 0.0
        $remaining = $null; $fullCap = $null; $tempC = $null; $cycle = $null

        if ($bs) {
            $chargeW = [math]::Round($bs.ChargeRate / 1000, 3)
            $disW    = [math]::Round($bs.DischargeRate / 1000, 3)
            $voltV   = [math]::Round($bs.Voltage / 1000, 3)
            if ($bs.Voltage -gt 0 -and ($bs.ChargeRate -ne 0 -or $bs.DischargeRate -ne 0)) {
                $amp = [math]::Round(($bs.ChargeRate + $bs.DischargeRate) / $bs.Voltage, 3)
            }
            $remaining = $bs.RemainingCapacity
        }

        $acState = 0
        $level = $null
        if ($wb) {
            $acState = [int]$wb.BatteryStatus
            if ($wb.EstimatedChargeRemaining -ne $null) { $level = [int]$wb.EstimatedChargeRemaining }
            if ($wb.FullChargeCapacity -ne $null) { $fullCap = [int]$wb.FullChargeCapacity }
        }

        # 电池温度 (十分之一开尔文)
        $bt = Get-CimInstance -Namespace root\WMI -ClassName BatteryTemperature
        if ($bt -and $bt.Temperature -gt 0) {
            $tempC = [math]::Round(($bt.Temperature / 10.0) - 273.15, 1)
        }

        # 循环次数
        $bc = Get-CimInstance -Namespace root\WMI -ClassName BatteryCycleCount
        if ($bc) { $cycle = [int]$bc.CycleCount }

        $stateCode = if ($acCodeMap.ContainsKey($acState)) { $acCodeMap[$acState] } else { "UNKNOWN_$acState" }

        $obj = [ordered]@{
            ts        = [DateTimeOffset]::Now.ToUnixTimeMilliseconds()
            chargeW   = $chargeW
            dischargeW= $disW
            netW      = [math]::Round($chargeW - $disW, 3)
            voltV     = $voltV
            amp       = $amp
            acState   = $acState
            acCode    = $stateCode
            charging  = ($acState -eq 2 -or $acState -eq 6 -or $acState -eq 7)
            levelPct  = $level
            remaining = $remaining
            fullCap   = $fullCap
            tempC     = $tempC
            cycle     = $cycle
        }

        Write-Output (($obj | ConvertTo-Json -Compress))
    }
    catch {
        Write-Output '{"error":"sample failed"}'
    }

    Start-Sleep -Milliseconds ([int]($interval * 1000))
}
