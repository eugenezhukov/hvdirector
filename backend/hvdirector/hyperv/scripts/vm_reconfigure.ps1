# $A: id, vcpu, memory_mb, add_disks: [{path, size_gb}],
#     nics: [{op: add|set|remove, name, switch, vlan, mode, allowed, mac, connected}]
$vm = Get-HvdVm ([string]$A.id)
if ($A.vcpu -or $A.memory_mb) {
    if ([string]$vm.State -ne 'Off') { Fail 'VM_MUST_BE_OFF' 'Power off the VM to change vCPU or memory' }
    if ($A.vcpu) { Set-VMProcessor -VM $vm -Count ([int]$A.vcpu) }
    if ($A.memory_mb) { Set-VMMemory -VM $vm -DynamicMemoryEnabled $false -StartupBytes ([int64]$A.memory_mb * 1MB) }
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
