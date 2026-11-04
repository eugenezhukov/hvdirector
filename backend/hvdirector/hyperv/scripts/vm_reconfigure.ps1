# $A: id, vcpu, memory_mb, add_disks: [{path, size_gb}], resize_disks: [{path, size_gb}], remove_disks: [{path}],
#     nics: [{op: add|set|remove, name, switch, vlan, mode, allowed, mac, connected}]
# Order: compute, extend disks, add disks, adapters, remove disks (last, so a failure earlier keeps the disk).
$vm = Get-HvdVm ([string]$A.id)
$state = [string]$vm.State
# checks first: nothing is changed when the request cannot be applied
if ($A.vcpu -and $state -ne 'Off') { Fail 'VM_MUST_BE_OFF' 'Power off the VM to change the number of vCPUs' }
if ($A.resize_disks -or $A.remove_disks) {
    if (@(Get-VMSnapshot -VM $vm).Count -gt 0) { Fail 'VM_HAS_SNAPSHOTS' 'Delete the VM checkpoints before changing its disks' }
}
if ($A.vcpu) { Set-VMProcessor -VM $vm -Count ([int]$A.vcpu) }
if ($A.memory_mb) {
    $bytes = [int64]$A.memory_mb * 1MB
    if ($state -eq 'Off') {
        Set-VMMemory -VM $vm -DynamicMemoryEnabled $false -StartupBytes $bytes
    } else {
        # Runtime memory resize (static memory, Windows Server 2016+ host, guest support required)
        Set-VMMemory -VM $vm -StartupBytes $bytes
    }
}
foreach ($d in $A.resize_disks) {
    $dpath = [string]$d.path
    $bytes = [int64]$d.size_gb * 1GB
    $vhd = Get-VHD -Path $dpath -ErrorAction Stop
    if ([int64]$vhd.Size -lt $bytes) { Resize-VHD -Path $dpath -SizeBytes $bytes -ErrorAction Stop }
}
$disks = @()
foreach ($d in $A.add_disks) {
    $dpath = [string]$d.path
    if (Test-Path -LiteralPath $dpath) { Fail 'DISK_EXISTS' ("Disk file already exists: {0}" -f $dpath) }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dpath) | Out-Null
    New-VHD -Path $dpath -SizeBytes ([int64]$d.size_gb * 1GB) -Dynamic | Out-Null
    Add-VMHardDiskDrive -VM $vm -ControllerType SCSI -Path $dpath
    $disks += $dpath
}
$nicOut = @()
foreach ($n in $A.nics) {
    switch ([string]$n.op) {
        'add' {
            $nic = Add-VMNetworkAdapter -VM $vm -Name ([string]$n.name) -Passthru
            if ($n.mac) { Set-VMNetworkAdapter -VMNetworkAdapter $nic -StaticMacAddress ([string]$n.mac) }
            Set-HvdNicSwitch $nic $n
            Set-HvdNicVlan $nic $n
            $nicOut += @{ name = [string]$nic.Name; mac = [string]$nic.MacAddress }
        }
        'set' {
            $nic = Get-VMNetworkAdapter -VM $vm -Name ([string]$n.name) -ErrorAction Stop | Select-Object -First 1
            Set-HvdNicSwitch $nic $n
            Set-HvdNicVlan $nic $n
        }
        'remove' {
            Get-VMNetworkAdapter -VM $vm -Name ([string]$n.name) -ErrorAction SilentlyContinue | Remove-VMNetworkAdapter
        }
    }
}
foreach ($d in $A.remove_disks) {
    $dpath = [string]$d.path
    Get-VMHardDiskDrive -VM $vm | Where-Object { [string]$_.Path -eq $dpath } | Remove-VMHardDiskDrive -ErrorAction Stop
    if (Test-Path -LiteralPath $dpath) { Remove-Item -LiteralPath $dpath -Force -ErrorAction Stop }
}
if ($vm.IsClustered) {
    try {
        Import-Module FailoverClusters -ErrorAction Stop
        try {
            Update-ClusterVirtualMachineConfiguration -VMId $vm.Id -ErrorAction Stop | Out-Null
        } catch {
            Get-ClusterResource -ErrorAction Stop |
                Where-Object { [string]$_.ResourceType -eq 'Virtual Machine Configuration' -and [string]$_.OwnerGroup -eq [string]$vm.Name } |
                Update-ClusterVirtualMachineConfiguration -ErrorAction Stop | Out-Null
        }
    } catch { }
}
$vm = Get-VM -Id $vm.Id
$res = Get-HvdVmSummary $vm
$res.added_disks = $disks
$res.nics = $nicOut
return $res
