# Register the VM as a highly available cluster role (run on the node hosting the VM).
Import-Module FailoverClusters -ErrorAction Stop
$vm = Get-HvdVm ([string]$A.id)
$grp = Add-ClusterVirtualMachineRole -VirtualMachine ([string]$vm.Name) -Name ([string]$A.group) -ErrorAction Stop
return @{ group = [string]$grp.Name; owner = [string]$grp.OwnerNode.Name }
