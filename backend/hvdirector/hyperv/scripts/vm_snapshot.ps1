# $A: id, action (create|revert|delete), name, snapshot_id
$vm = Get-HvdVm ([string]$A.id)
switch ([string]$A.action) {
    'create' {
        $before = @(Get-VMSnapshot -VM $vm | ForEach-Object { [string]$_.Id })
        Checkpoint-VM -VM $vm -SnapshotName ([string]$A.name)
        $snap = Get-VMSnapshot -VM $vm | Where-Object { $before -notcontains [string]$_.Id } | Select-Object -First 1
        if (-not $snap) { Fail 'SNAPSHOT_FAILED' 'Checkpoint was not created' }
        return @{ id = [string]$snap.Id; name = [string]$snap.Name; created = $snap.CreationTime.ToUniversalTime().ToString('o') }
    }
    'revert' {
        $snap = Get-VMSnapshot -VM $vm | Where-Object { [string]$_.Id -eq [string]$A.snapshot_id } | Select-Object -First 1
        if (-not $snap) { Fail 'SNAPSHOT_NOT_FOUND' ("Checkpoint {0} not found" -f $A.snapshot_id) }
        Restore-VMSnapshot -VMSnapshot $snap -Confirm:$false
        $vm = Get-VM -Id $vm.Id
        return @{ state = [string]$vm.State }
    }
    'delete' {
        $snap = Get-VMSnapshot -VM $vm | Where-Object { [string]$_.Id -eq [string]$A.snapshot_id } | Select-Object -First 1
        if ($snap) { Remove-VMSnapshot -VMSnapshot $snap -Confirm:$false }
        return @{ deleted = [bool]$snap }
    }
    default { Fail 'BAD_ACTION' ("Unknown snapshot action {0}" -f $A.action) }
}
