# Insert / eject ISO.  $A: id, iso (UNC path or null)
$vm = Get-HvdVm ([string]$A.id)
$dvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
if (-not $dvd) {
    if ([int]$vm.Generation -eq 2) { $dvd = Add-VMDvdDrive -VM $vm -Passthru }
    else { $dvd = Add-VMDvdDrive -VM $vm -ControllerNumber 1 -ControllerLocation 0 -Passthru }
}
if ($A.iso) { Set-VMDvdDrive -VMDvdDrive $dvd -Path ([string]$A.iso) }
else { Set-VMDvdDrive -VMDvdDrive $dvd -Path $null }
if ($A.iso -and [int]$vm.Generation -eq 2 -and [string]$vm.State -eq 'Off') {
    # Boot from the inserted media first so that the OS installer starts.
    $fwDvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
    try { Set-VMFirmware -VM $vm -FirstBootDevice $fwDvd } catch { }
}
$dvd = Get-VMDvdDrive -VM $vm | Select-Object -First 1
return @{ dvd = [string]$dvd.Path }
