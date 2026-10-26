// Azure VM for the Knowledge Vault demo (option C in demo/deploy/README.md): Ubuntu 24.04 with Docker,
// public IP with a DNS label, NSG for SSH/HTTP/HTTPS. Deployed by .github/workflows/infra.yml:
//   az deployment group create -g <rg> -f infra/main.bicep -p dnsLabel=<label> adminSshPublicKey="<key>"
@description('Prefix for resource names')
param namePrefix string = 'kvdemo'
@description('DNS label for the public IP: <dnsLabel>.<region>.cloudapp.azure.com (must be unique in the region)')
param dnsLabel string
@description('SSH public key for the admin user (the deploy workflow connects with the matching private key)')
param adminSshPublicKey string
param adminUsername string = 'kvdemo'
param vmSize string = 'Standard_D4s_v5'
param osDiskSizeGB int = 128
@description('CIDR allowed to reach SSH (22); keep narrow — the deploy runner needs it, or use Azure Bastion')
param sshSourceCidr string = '*'
param location string = resourceGroup().location

var cloudInit = loadFileAsBase64('cloud-init.yaml')

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: '${namePrefix}-nsg'
  location: location
  properties: {
    securityRules: [
      { name: 'ssh', properties: { priority: 1000, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: sshSourceCidr, sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '22' } }
      { name: 'http', properties: { priority: 1010, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '80' } }
      { name: 'https', properties: { priority: 1020, direction: 'Inbound', access: 'Allow', protocol: 'Tcp', sourceAddressPrefix: '*', sourcePortRange: '*', destinationAddressPrefix: '*', destinationPortRange: '443' } }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${namePrefix}-vnet'
  location: location
  properties: {
    addressSpace: { addressPrefixes: [ '10.40.0.0/24' ] }
    subnets: [ { name: 'default', properties: { addressPrefix: '10.40.0.0/24', networkSecurityGroup: { id: nsg.id } } } ]
  }
}

resource pip 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: '${namePrefix}-pip'
  location: location
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: { domainNameLabel: dnsLabel }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: '${namePrefix}-nic'
  location: location
  properties: {
    ipConfigurations: [ {
      name: 'ipconfig1'
      properties: {
        subnet: { id: vnet.properties.subnets[0].id }
        privateIPAllocationMethod: 'Dynamic'
        publicIPAddress: { id: pip.id }
      }
    } ]
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: '${namePrefix}-vm'
  location: location
  properties: {
    hardwareProfile: { vmSize: vmSize }
    osProfile: {
      computerName: '${namePrefix}-vm'
      adminUsername: adminUsername
      customData: cloudInit
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: { publicKeys: [ { path: '/home/${adminUsername}/.ssh/authorized_keys', keyData: adminSshPublicKey } ] }
      }
    }
    storageProfile: {
      imageReference: { publisher: 'Canonical', offer: 'ubuntu-24_04-lts', sku: 'server', version: 'latest' }
      osDisk: { createOption: 'FromImage', diskSizeGB: osDiskSizeGB, managedDisk: { storageAccountType: 'Premium_LRS' } }
    }
    networkProfile: { networkInterfaces: [ { id: nic.id } ] }
  }
}

output fqdn string = pip.properties.dnsSettings.fqdn
output publicIp string = pip.properties.ipAddress
output sshCommand string = 'ssh ${adminUsername}@${pip.properties.dnsSettings.fqdn}'
