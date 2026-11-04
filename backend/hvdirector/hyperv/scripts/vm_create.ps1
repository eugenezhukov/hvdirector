# Create a VM on the local host.
# $A: name, generation, vcpu, memory_mb, path (folder that will contain the VM folder), notes, boot_disk_first,
#     secure_boot (off|windows|linux), iso (UNC path or null),
#     disks: [{path, size_gb}], nics: [{name, switch, vlan, mode, allowed, mac, connected}]
$name = [string]$A.name
if (Get-VM -Name $name -ErrorAction SilentlyContinue) { Fail 'VM_EXISTS' ("VM '{0}' already exists on {1}" -f $name, $env:COMPUTERNAME) }
$gen = [int]$A.generation
$memBytes = [int64]$A.memory_mb * 1MB

foreach ($d in $A.disks) {
    if (Test-Path -LiteralPath ([string]$d.path)) { Fail 'DISK_EXISTS' ("Disk file already exists: {0}" -f $d.path) }
}

New-Item -ItemType Directory -Force -Path ([string]$A.path) | Out-Null
$vm = New-VM -Name $name -Generation $gen -MemoryStartupBytes $memBytes -Path ([string]$A.path) -NoVHD
$createdDisks = @()
try {
    Set-VM -VM $vm -ProcessorCount ([int]$A.vcpu) -StaticMemory -AutomaticStartAction Nothing -AutomaticStopAction ShutDown -Notes ([string]$A.notes)
    try { Set-VM -VM $vm -CheckpointType Production } catch { }
    Get-VMNetworkAdapter -VM $vm | Remove-VMNetworkAdapter

    $i = 0
    foreach ($d in $A.disks) {
        $dpath = [string]$d.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dpath) | Out-Null
        New-VHD -Path $dpath -SizeBytes ([int64]$d.size_gb * 1GB) -Dynamic | Out-Null
        $createdDisks += $dpath
        if ($gen -eq 1 -and $i -eq 0) {
            Add-VMHardDiskDrive -VM $vm -ControllerType IDE -ControllerNumber 0 -ControllerLocation 0 -Path $dpath
        } else {
            Add-VMHardDiskDrive -VM $vm -ControllerType SCSI -Path $dpath
        }
        $i++
    }

    if ($gen -eq 2) {
        $dvd = Add-VMDvdDrive -VM $vm -Passthru
    } else {
        $dvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
        if (-not $dvd) { $dvd = Add-VMDvdDrive -VM $vm -ControllerNumber 1 -ControllerLocation 0 -Passthru }
    }
    if ($A.iso) { Set-VMDvdDrive -VMDvdDrive $dvd -Path ([string]$A.iso) }

    $nicOut = @()
    foreach ($n in $A.nics) {
        $nic = Add-VMNetworkAdapter -VM $vm -Name ([string]$n.name) -Passthru
        if ($n.mac) { Set-VMNetworkAdapter -VMNetworkAdapter $nic -StaticMacAddress ([string]$n.mac) }
        Set-HvdNicSwitch $nic $n
        Set-HvdNicVlan $nic $n
        $nic = Get-VMNetworkAdapter -VM $vm -Name ([string]$n.name) | Select-Object -First 1
        $nicOut += @{ name = [string]$nic.Name; mac = [string]$nic.MacAddress }
    }

    if ($gen -eq 2) {
        switch ([string]$A.secure_boot) {
            'off'   { Set-VMFirmware -VM $vm -EnableSecureBoot Off }
            'linux' { Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate 'MicrosoftUEFICertificateAuthority' }
            default { Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate 'MicrosoftWindows' }
        }
        $hdd = Get-VMHardDiskDrive -VM $vm | Select-Object -First 1
        $fwDvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
        $net = Get-VMNetworkAdapter -VM $vm | Select-Object -First 1
        $order = @()
        if ($A.boot_disk_first) {
            # Empty disk is skipped by UEFI, so the installer boots from DVD; after the
            # installation the VM boots from disk without ejecting the media first.
            if ($hdd) { $order += $hdd }
            if ($fwDvd) { $order += $fwDvd }
        } else {
            if ($A.iso -and $fwDvd) { $order += $fwDvd }
            if ($hdd) { $order += $hdd }
            if ((-not $A.iso) -and $fwDvd) { $order += $fwDvd }
            if ($net) { $order += $net }
        }
        if ($order.Count -gt 0) { Set-VMFirmware -VM $vm -BootOrder $order }
    } else {
        if ($A.iso) { Set-VMBios -VM $vm -StartupOrder @('CD', 'IDE', 'LegacyNetworkAdapter', 'Floppy') }
        else { Set-VMBios -VM $vm -StartupOrder @('IDE', 'CD', 'LegacyNetworkAdapter', 'Floppy') }
    }
} catch {
    $err = $_
    try { Remove-VM -VM $vm -Force -ErrorAction SilentlyContinue } catch { }
    foreach ($p in $createdDisks) { try { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } catch { } }
    throw $err
}
$vm = Get-VM -Id $vm.Id
return @{
    id          = [string]$vm.Id
    name        = [string]$vm.Name
    state       = [string]$vm.State
    config_path = [string]$vm.ConfigurationLocation
    nics        = $nicOut
    host        = [string]$env:COMPUTERNAME
}
