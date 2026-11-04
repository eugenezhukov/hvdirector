$vms = @()
foreach ($vm in @(Get-VM)) { $vms += (Get-HvdVmSummary $vm) }
return @{ host = [string]$env:COMPUTERNAME; vms = $vms }
