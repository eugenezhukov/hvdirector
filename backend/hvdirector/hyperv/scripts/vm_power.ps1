# $A: id, action (start|shutdown|poweroff|reset|reboot|save|resume)
$vm = Get-HvdVm ([string]$A.id)
$state = [string]$vm.State
switch ([string]$A.action) {
    'start' {
        if ($state -eq 'Paused') { Resume-VM -VM $vm }
        elseif ($state -ne 'Running') { Start-VM -VM $vm }
    }
    'shutdown' {
        if ($state -eq 'Running') { Stop-VM -VM $vm -Force }
    }
    'poweroff' {
        if ($state -ne 'Off') { Stop-VM -VM $vm -TurnOff -Force }
    }
    'reset' {
        if ($state -eq 'Running') { Restart-VM -VM $vm -Force } else { Fail 'VM_NOT_RUNNING' 'VM is not running' }
    }
    'reboot' {
        if ($state -ne 'Running') { Fail 'VM_NOT_RUNNING' 'VM is not running' }
        $cmd = Get-Command Restart-VM
        if ($cmd.Parameters.ContainsKey('Type')) {
            Restart-VM -VM $vm -Type Reboot -Force
        } else {
            Stop-VM -VM $vm -Force
            Start-VM -VM $vm
        }
    }
    'save' {
        if ($state -eq 'Running') { Save-VM -VM $vm }
    }
    'resume' {
        if ($state -eq 'Paused') { Resume-VM -VM $vm }
    }
    default { Fail 'BAD_ACTION' ("Unknown power action {0}" -f $A.action) }
}
$vm = Get-VM -Id $vm.Id
return (Get-HvdVmSummary $vm)
