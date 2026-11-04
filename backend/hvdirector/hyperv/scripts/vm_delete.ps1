# $A: id, group, disk_paths[], folders[]
Import-Module FailoverClusters -ErrorAction SilentlyContinue
$vm = $null
if ($A.id) { try { $vm = Get-VM -Id ([string]$A.id) -ErrorAction Stop } catch { $vm = $null } }
if ($vm -and [string]$vm.State -ne 'Off') { Stop-VM -VM $vm -TurnOff -Force }
if ($A.group) {
    $g = Get-ClusterGroup -Name ([string]$A.group) -ErrorAction SilentlyContinue
    if ($g) {
        Stop-ClusterGroup -InputObject $g -ErrorAction SilentlyContinue | Out-Null
        Remove-ClusterGroup -InputObject $g -RemoveResources -Force
    }
}
if ($vm) {
    $vm = Get-VM -Id $vm.Id -ErrorAction SilentlyContinue
    if ($vm) { Remove-VM -VM $vm -Force }
}
$removed = @()
foreach ($p in $A.disk_paths) {
    if ($p -and (Test-Path -LiteralPath ([string]$p))) { Remove-Item -LiteralPath ([string]$p) -Force; $removed += [string]$p }
}
foreach ($f in $A.folders) {
    if ($f -and (Test-Path -LiteralPath ([string]$f))) { Remove-Item -LiteralPath ([string]$f) -Recurse -Force; $removed += [string]$f }
}
return @{ removed = $removed }
