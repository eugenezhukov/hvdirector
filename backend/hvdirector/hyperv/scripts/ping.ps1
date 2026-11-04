# Connectivity / credentials check.
$hv = $null
try { $hv = Get-VMHost -ErrorAction Stop } catch { }
return @{ name = [string]$env:COMPUTERNAME; hyperv = [bool]$hv; ps_version = [string]$PSVersionTable.PSVersion }
