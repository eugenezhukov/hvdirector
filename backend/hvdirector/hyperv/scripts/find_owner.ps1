# Locate the cluster node that currently owns a clustered VM role.
Import-Module FailoverClusters -ErrorAction Stop
$g = $null
if ($A.group) { $g = Get-ClusterGroup -Name ([string]$A.group) -ErrorAction SilentlyContinue }
if (-not $g -and $A.vm_id) {
    foreach ($cand in @(Get-ClusterGroup | Where-Object { [string]$_.GroupType -eq 'VirtualMachine' })) {
        foreach ($r in @($cand | Get-ClusterResource | Where-Object { [string]$_.ResourceType -eq 'Virtual Machine' })) {
            $p = $r | Get-ClusterParameter -Name VmID -ErrorAction SilentlyContinue
            if ($p -and ([string]$p.Value -eq [string]$A.vm_id)) { $g = $cand; break }
        }
        if ($g) { break }
    }
}
if (-not $g) { Fail 'GROUP_NOT_FOUND' ("Cluster role for VM {0} not found" -f $A.vm_id) }
return @{ group = [string]$g.Name; owner = [string]$g.OwnerNode.Name; state = [string]$g.State }
