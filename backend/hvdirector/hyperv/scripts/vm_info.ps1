$vm = Get-HvdVm ([string]$A.id)
$info = Get-HvdVmSummary $vm
$snaps = @()
foreach ($s in @(Get-VMSnapshot -VM $vm)) {
    $snaps += @{ id = [string]$s.Id; name = [string]$s.Name; created = $s.CreationTime.ToUniversalTime().ToString('o'); parent = [string]$s.ParentSnapshotId }
}
$nics = @()
foreach ($a in @(Get-VMNetworkAdapter -VM $vm)) {
    $vlan = Get-VMNetworkAdapterVlan -VMNetworkAdapter $a
    $nics += @{ name = [string]$a.Name; mac = [string]$a.MacAddress; switch = [string]$a.SwitchName; vlan = [int]$vlan.AccessVlanId; ips = @($a.IPAddresses | ForEach-Object { [string]$_ }) }
}
$dvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
$info.snapshots = $snaps
$info.nics = $nics
$info.dvd = if ($dvd) { [string]$dvd.Path } else { $null }
$info.heartbeat = [string]$vm.Heartbeat
$info.generation = [int]$vm.Generation
$info.vcpu = [int]$vm.ProcessorCount
$info.memory_startup_mb = (To-MB $vm.MemoryStartup)
return $info
