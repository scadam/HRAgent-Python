@minLength(3)
@maxLength(22)
param resourceBaseName string
param location string
@allowed([
  'FC1'
])
param functionAppSKU string
@allowed([
  '512'
  '2048'
  '4096'
])
param instanceMemoryMB string = '512'
param maximumInstanceCount string = '40'

var storageName = toLower('${resourceBaseName}sa')
var planName = functionAppSKU == 'FC1' ? '${resourceBaseName}-flexplan' : '${resourceBaseName}-plan'
var functionName = '${resourceBaseName}-func'
var hostingTier = 'FlexConsumption'
var deploymentContainerName = toLower('${resourceBaseName}-code')
var storageSuffix = environment().suffixes.storage
var blobEndpoint = 'https://${storage.name}.blob.${storageSuffix}'
var queueEndpoint = 'https://${storage.name}.queue.${storageSuffix}'
var tableEndpoint = 'https://${storage.name}.table.${storageSuffix}'
var deploymentContainerUrl = '${blobEndpoint}/${deploymentContainerName}'

resource storage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  name: 'default'
  parent: storage
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  name: deploymentContainerName
  parent: blobService
  properties: {
    publicAccess: 'None'
  }
}

resource serverFarm 'Microsoft.Web/serverfarms@2022-09-01' = {
  name: planName
  location: location
  sku: {
    name: functionAppSKU
    tier: hostingTier
  }
  kind: 'functionapp'
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionName
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    reserved: true
    serverFarmId: serverFarm.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: deploymentContainerUrl
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.10'
      }
      scaleAndConcurrency: {
        instanceMemoryMB: int(instanceMemoryMB)
        maximumInstanceCount: int(maximumInstanceCount)
      }
    }
    siteConfig: {
      appSettings: [
        {
          name: 'FUNCTIONS_EXTENSION_VERSION'
          value: '~4'
        }
        {
          name: 'AzureWebJobsStorage__accountName'
          value: storage.name
        }
        {
          name: 'AzureWebJobsStorage__blobServiceUri'
          value: blobEndpoint
        }
        {
          name: 'AzureWebJobsStorage__queueServiceUri'
          value: queueEndpoint
        }
        {
          name: 'AzureWebJobsStorage__tableServiceUri'
          value: tableEndpoint
        }
        {
          name: 'AzureWebJobsStorage__credential'
          value: 'managedIdentity'
        }
        {
          name: 'AzureWebJobsScriptRoot'
          value: '/home/site/wwwroot/src'
        }
        {
          name: 'PYTHONPATH'
          value: '/home/site/wwwroot/src'
        }
        {
          name: 'AzureWebJobsFeatureFlags'
          value: 'EnableWorkerIndexing'
        }
      ]
      ftpsState: 'FtpsOnly'
    }
  }
  dependsOn: [
    deploymentContainer
  ]
}

resource storageBlobContributor 'Microsoft.Authorization/roleAssignments@2020-04-01-preview' = {
  name: guid(functionApp.id, 'blob-contributor')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storageQueueContributor 'Microsoft.Authorization/roleAssignments@2020-04-01-preview' = {
  name: guid(functionApp.id, 'queue-contributor')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource storageTableContributor 'Microsoft.Authorization/roleAssignments@2020-04-01-preview' = {
  name: guid(functionApp.id, 'table-contributor')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3')
    principalId: functionApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var apiEndpoint = 'https://${functionApp.properties.defaultHostName}'

output FUNC_ENDPOINT string = apiEndpoint
output API_FUNCTION_ENDPOINT string = apiEndpoint
output API_FUNCTION_RESOURCE_ID string = functionApp.id
output OPENAPI_SERVER_URL string = apiEndpoint
output OPENAPI_SERVER_DOMAIN string = functionApp.properties.defaultHostName
output FUNCTION_APP_NAME string = functionApp.name
