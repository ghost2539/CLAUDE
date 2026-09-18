# API da Omnissa (Workspace ONE UEM) — o que dá para automatizar

Levantado em 18/09/2026 a partir das quatro especificações OpenAPI do
tenant (`mdmv1.json`, `mdmv2.json`, `mdmv3.json`, `mdmv4.json`). As quatro
declaram o mesmo servidor:

```
servers: https://as258.awmdm.com/api/mdm
```

Resposta curta às três perguntas: **dá para consultar a base de coletores,
dá para incluir e remover tag, e dá para excluir device** — esta última
existe e está documentada, e continua fora do que se testa.

---

## 1. Dois produtos, dois hosts, duas autenticações

| | Omnissa Intelligence | Workspace ONE UEM |
|---|---|---|
| Host | `api.<regiao>.data.workspaceone.com` | `as258.awmdm.com/api/mdm` |
| Autenticação | OAuth `client_credentials` (JSON de service account) | **Basic** + `aw-tenant-code` |
| Serve para | relatórios (assíncrono: cria, roda, baixa CSV) | busca, tag, exclusão, comandos |
| Tem tag? | **não** | sim |

A documentação entregue primeiro (o PDF de 52 páginas) era a do
**Intelligence** — por isso a conclusão anterior de que tag e exclusão não
apareciam. Elas não aparecem *lá*. Estão na UEM, que é esta API.

**Atenção à letra do host.** O console é `cn258.awmdm.com` — é o que está
em `MDM_BASE_URL` e de onde hoje se raspa a grade HTML dos coletores. A API
é `as258.awmdm.com`. Mesmo tenant, hosts diferentes.

## 2. Autenticação

Toda operação declara as mesmas três alternativas:

```json
"security": [ {"BasicAuth": []}, {"CmsAuth": []}, {"ApiKeyAuth": []} ]
```

* `BasicAuth` — `Authorization: Basic base64(usuario:senha)`
* `ApiKeyAuth` — header `aw-tenant-code`
* `GroupIdAuth` — header `aw-groupid`
* `CmsAuth` — header `Authorization` no esquema CMS

Na prática a UEM quer **Basic e `aw-tenant-code` juntos**: o usuário diz
quem é, a chave do tenant diz para qual instalação. A chave sai no console
em *Groups & Settings → All Settings → System → Advanced → API → REST API*.

A versão da API vai no **Accept**, não no caminho:

```
Accept: application/json;version=1
```

### A chave do Intelligence serve para descobrir o `aw-tenant-code`?

**Não.** São duas credenciais de dois produtos. A do Intelligence é OAuth
`client_credentials` (clientId + clientSecret → JWT, com alcance dado pelo
campo `resourceIds`); o `aw-tenant-code` é uma string configurada no console
da UEM, por organization group. Não há endpoint que devolva um a partir do
outro, e o JWT não carrega essa informação.

Duas saídas, que valem mais do que caçar quem tinha a chave:

1. **A chave não é de ninguém.** É do organization group, e fica no console
   em *Groups & Settings → All Settings → System → Advanced → API → REST
   API*. Quem tiver o papel de admin lê ali. Se alguma integração da casa já
   chama esta API, ela também está na configuração dessa integração.
2. **Talvez nem seja necessária.** Se o token do Intelligence for aceito
   pela UEM, o `aw-tenant-code` deixa de fazer falta. Isso **não** está na
   especificação — ela declara Basic, ApiKey, GroupId e Cms, e nenhum
   Bearer — mas custa uma requisição descobrir:
   `--credencial cred.json --bearer`. Um 401 ali não condena a chave; diz
   que este caminho não existe nesta instalação.

O campo que decide o alcance do token é o `resourceIds` do arquivo de
service account: se ali só constar o recurso do Intelligence, a UEM recusa
por mais correta que a chave esteja. A sonda imprime esse campo antes de
tentar, para que o 401 tenha explicação em vez de virar mistério.

### `errorCode 1005` — o que é e o que não é

```json
{"errorCode":1005,"message":"An error occurred while validating remote
 service client credentials or user not found : renner\\001200660"}
```

Primeiro, o que **não** é problema: a barra dupla é só o JSON escapando uma
barra literal, e o fato de a UEM devolver um erro estruturado com o nome do
usuário prova que a requisição **chegou na API** — host e caminho certos.

O que é: a recusa do **serviço REST**, que não é o mesmo portão do console.
Três causas, e a segunda engana mais que as outras duas juntas:

1. **falta o `aw-tenant-code`**;
2. **a conta é de diretório (AD).** O Basic da API da UEM quer uma conta de
   admin **do tipo Basic**, criada dentro da UEM. Entrar no console com a
   conta do AD não implica que ela autentique na API — são caminhos de
   autenticação diferentes;
3. a conta existe, mas **não tem papel com acesso de API**.

Nenhuma das três se resolve trocando a senha. O pedido certo ao time que
administra o MDM é: *"uma conta de serviço do tipo Basic, com papel de API,
no organization group X"* — e junto vem o `aw-tenant-code`, que é do
organization group.

Por isso a sonda **para no primeiro 401**: a primeira prova já respondeu, e
insistir nas outras só soma tentativas de login falhas contra o AD.

### Qual credencial usar

O portal **já tem uma credencial de serviço do MDM no cofre**
(`MDM_USUARIO` / `MDM_SENHA`, usada por `routers/obsolescencia.py` para
entrar no console). É essa que deve ser usada aqui — não a senha pessoal de
ninguém. Motivos, na ordem em que importam:

1. senha pessoal em serviço vira segredo compartilhado: ninguém consegue
   mais dizer quem fez o quê no log do MDM;
2. troca de senha do usuário (ou saída da pessoa da empresa) derruba a
   automação sem aviso;
3. cada tentativa errada conta como falha de login no AD — um laço com
   senha vencida bloqueia a conta da pessoa.

O acesso de API costuma ser um **papel à parte** do acesso ao console: pode
acontecer de a credencial entrar no console e levar 401 aqui. Se for o
caso, é pedir o papel de API para essa conta — não trocar de conta.

## 3. Consultar a base de coletores

```
GET /api/mdm/devices/search?pagesize=500&page=0        (v1 e v2)
    filtros: user, model, platform, lastseen, ownership, lgid,
             compliantstatus, seensince, orderby, sortorder

GET /api/mdm/devices/extensivesearch                    (v1)
    filtros: organizationgroupid, platform, startdatetime, enddatetime,
             deviceid, customattributes, enrollmentstatus,
             statuschangestarttime, statuschangeendtime, macaddress

GET /api/mdm/devices?searchby=Serialnumber&id=<serie>   (v1)
    searchby aceita: Macaddress, Udid, Serialnumber, ImeiNumber, EasId
```

Campos do device incluem `SerialNumber`, `AssetNumber`, `Udid`,
`DeviceFriendlyName`, `UserName`, `Model`, `Platform`, `OperatingSystem`,
`LastSeen`, `EnrollmentStatus`, `ComplianceStatus`, `LocationGroupName`.

`pagesize` padrão é 500, `page` é base zero — o mesmo formato que a
varredura da grade HTML já usa hoje em `integracoes/mdm_airwatch.py`. Trocar
a raspagem de HTML por esta busca elimina a parte mais frágil daquele
módulo: parsear célula de tabela.

## 4. Tags

Achar a tag:

```
GET /api/mdm/tags/search?name=<nome>&organizationgroupid=<og>
GET /api/mdm/devices/{uuid}/tags          tags de um device
GET /api/mdm/tags/{tagId}/devices         devices de uma tag
```

Um device por vez (v1 — o caminho mais seguro, porque não tem como pedir
outra coisa):

```
POST   /api/mdm/devices/{device_uuid}/tags/{tag_uuid}      → 201
DELETE /api/mdm/devices/{device_uuid}/tags/{tag_uuid}      → 204
```

Em lote:

```
POST   /api/mdm/tags/{tagid}/adddevices        (v1)
POST   /api/mdm/tags/{tagid}/removedevices     (v1)
POST   /api/mdm/tags/{tagUuid}/devices         (v2, incluir)
DELETE /api/mdm/tags/{tagUuid}/devices         (v2, remover)
```

Corpo do lote (`BulkInput`, o mesmo nos quatro):

```json
{ "BulkValues": { "Value": ["<id>", "<id>", "..."] } }
```

## 5. Exclusão de device — existe, e fica de fora

Está documentada. Não é exercitada por nenhum script daqui, a pedido:

```
DELETE /api/mdm/devices/{id}
DELETE /api/mdm/devices?searchby=Serialnumber&id=<serie>
POST   /api/mdm/devices/bulk?searchby=Serialnumber      ← APAGA VÁRIOS
```

## 6. Duas armadilhas

**`POST /devices/bulk` tem cara de cadastro e apaga.** A descrição da
própria API é *"Deletes multiple devices identified by device id or
alternate id"*. É um POST, com corpo `BulkInput` — exatamente o mesmo
formato do corpo que inclui tag em lote. Um caminho trocado entre as duas
chamadas apaga o parque em vez de marcá-lo.

**Na V4, uma chamada só faz tudo.** `DeviceActionRequestV4Model` e
`DeviceBulkActionRequestV4Model` têm um campo `action_name` cujo enum
inclui, lado a lado:

```
MANAGE_TAGS, LOCK_DEVICE, SEND_MESSAGE, REBOOT, CHANGE_ORGANIZATION_GROUP,
DEVICE_DELETE, DEVICE_WIPE, DEVICE_ENTERPRISE_WIPE, ENTERPRISE_RESET, ...
```

Uma string errada nesse campo é a diferença entre marcar uma tag e apagar o
aparelho. Duas consequências práticas:

1. **usar as rotas v1/v2 por assunto**, não a ação genérica da V4: o
   caminho `/tags/{id}/adddevices` não tem como virar exclusão;
2. se um dia a V4 for necessária, **lista de permitidos** no portal
   (`{"MANAGE_TAGS"}`), nunca repassar para a API o que veio da tela.

A V4 tem uma rede de proteção própria que vale conhecer:
`requested_device_count` — o servidor recusa se o conjunto de devices que o
filtro resolveu divergir da contagem esperada além da tolerância.

Nota sobre o `mdmv4.json`: ele traz os *modelos* da V4, mas o único caminho
publicado nele é `GET /profiles/{profileId}`. Os endpoints que consomem
esses modelos não estão no arquivo. Já o `/devices/action` da **v3** existe
(`POST /api/mdm/devices/action`), e o enum dele é bem menor — só
`MIGRATE_TO_MULTIUSER`, `CHANGE_TO_SINGLEUSER`, `CHANGE_TO_MULTIUSER`,
`STEP_UP_TO_HUB_MANAGED`. Ou seja: tag por ação genérica **não** está
disponível na v3 deste tenant; é pelas rotas de tag mesmo.

## 7. Como provar contra o ambiente real

```bash
# só leitura; usa a credencial de serviço do cofre
python3 scripts/testar_omnissa_mdm.py --uem as258.awmdm.com --basic

# procurando um coletor pela série
python3 scripts/testar_omnissa_mdm.py --uem as258.awmdm.com --basic --serie ABC123

# a referência viva da instalação
python3 scripts/testar_omnissa_mdm.py --uem as258.awmdm.com --basic --descobrir

# sem o aw-tenant-code: o token do Intelligence vale na UEM?
python3 scripts/testar_omnissa_mdm.py --bearer
```

### Como informar cada credencial

São **duas**, de dois produtos, e nenhuma das duas entra por argumento da
linha de comando: `ps` mostra o comando inteiro para qualquer um logado na
máquina, e o valor ficaria no histórico do shell.

**UEM (`--basic`)** — usuário e senha. A sonda procura nesta ordem:

1. `OMNISSA_UEM_USUARIO` e `OMNISSA_UEM_SENHA` no ambiente;
2. o **cofre do portal** (`MDM_USUARIO` / `MDM_SENHA`) — rodando no
   servidor, não é preciso informar nada: é a credencial de serviço que já
   entra no console hoje;
3. digitado na hora (a senha não é ecoada).

**Intelligence (`--bearer` e `--intelligence`)** — `clientId`,
`clientSecret` e `tokenEndpoint`. Ou o arquivo que o console baixou:

```bash
python3 scripts/testar_omnissa_mdm.py --credencial /caminho/arquivo.json --bearer
```

Ou, se a chave veio em pedaços soltos (num chamado, num chat), sem arquivo
nenhum:

```bash
export OMNISSA_CLIENT_ID=...
export OMNISSA_TOKEN_ENDPOINT=https://<regiao>.uemauth...com/connect/token
read -rs OMNISSA_CLIENT_SECRET && export OMNISSA_CLIENT_SECRET   # não ecoa
python3 scripts/testar_omnissa_mdm.py --bearer
```

O `tokenEndpoint` acompanha a chave quando ela é emitida — sem ele não há
para onde pedir o token, e nenhum valor padrão serve porque ele varia por
região.

> Um `export` fica valendo para a sessão inteira do shell. Terminado o
> teste, `unset OMNISSA_CLIENT_SECRET OMNISSA_UEM_SENHA`.

`scripts/verificar_omnissa_mdm.py` prova que a sonda não escreve: todas as
chamadas são GET, nenhum caminho de exclusão é tocado, a senha não passa
por argumento e o TLS não é desligado. Três dessas verificações são
contraprovas — refazem o erro e exigem recusa.

---

## Inventário das rotas de device, tag, grupo e usuário

Gerado das quatro especificações. `New - ` foi retirado dos resumos.

### v1 — 166 operações de device/tag/grupo

```
GET     /devices                                                   Get Device details by Alternate id.
PUT     /devices                                                   Edit the device details identified by alternate id for Devic
POST    /devices                                                   Retrieves information about multiple devices identified by t
DELETE  /devices                                                   Deletes Device details by alternate id for Device.
POST    /devices/admin-actions/{adminAction}                       Executes the admin action for the set of devices after perfo
GET     /devices/apps                                              Retrieves application details of the device identified by al
GET     /devices/appstatus                                         Gets App Status for a combination of input elements.
PUT     /devices/assetnumber/{assetnumber}/customattributes        Update device custom attributes by asset number.
POST    /devices/bulk                                              Deletes multiple devices identified by device id or alternat
GET     /devices/bulksettings                                      Retrieve limits for bulk actions.
GET     /devices/certificates                                      Retrieves certificate details of the device identified by th
POST    /devices/commands                                          Executes a command for device by alternate ID.
POST    /devices/commands/bulk                                     Executes command for multiple devices identified by alternat
POST    /devices/commands/bulk/scheduleosupdate                    Executes Schedule OS Update command for devices in bulk.
POST    /devices/commands/changeorganizationgroup                  Changes the organization group to which the device identifie
POST    /devices/commands/changepasscode                           Executes command for change passcode of device by alternate 
POST    /devices/commands/containerpasscode                        Executes change passcode command for container device matchi
POST    /devices/commands/finddevice                               Executes finddevice command for device by alternate id.
POST    /devices/commands/remoteview                               Executes start remoteview command for device matching the fi
POST    /devices/commands/requestdevicelog                         Executes device log request command for device matching the 
POST    /devices/commands/scheduleosupdate                         Schedule OS Update for supervised DEP devices
POST    /devices/commands/stopdevicelog                            Executes stop device log request command for device.
GET     /devices/compliance                                        Retrieves compliance details of the device identified by the
POST    /devices/computer-sid/bulk                                 Bulk updates Computer SID attribute identified by device id 
GET     /devices/content                                           Retrieves the content details of the device identified by al
GET     /devices/customattribute/changereport                      Searches for changes made to device custom attributes.
GET     /devices/customattribute/search                            Searches for device custom attributes.
PUT     /devices/customattributes                                  Bulk update of device custom attributes.
GET     /devices/devicecountinfo                                   Retrieves Device Count Information which are Categorized by 
POST    /devices/enrolleddevicescount                              Retrieves Count of all enrolled devices based on any or all 
GET     /devices/eventlog                                          Retrieves events corresponding to the device identified by a
GET     /devices/extensivesearch                                   Extensive search of device details.
GET     /devices/gps                                               Retrieves the GPS coordinates of the device identified by al
POST    /devices/gps                                               Executes bulk gps coordinates by device and alternate id.
POST    /devices/gps/search                                        Retrieves the GPS coordinates of multiple devices within the
POST    /devices/id                                                Retrieves information about multiple devices identified by d
GET     /devices/litesearch                                        Searches devices and its custom attributes.
POST    /devices/managedsettings                                   Sets the managed settings for an iOS device based on alterna
GET     /devices/manufacturers/{manufacturerId}/models             Get the associated Models by Manufacturer (OEM).
POST    /devices/messages/bulkemail                                Sends an email to the users of multiple devices.
POST    /devices/messages/bulkpush                                 Sends a push message to multiple devices.
POST    /devices/messages/bulksms                                  Sends an SMS message to multiple devices.
POST    /devices/messages/email                                    Sends an email to the user of the device.
POST    /devices/messages/message                                  Sends a message to the device.
POST    /devices/messages/push                                     Sends a push message to the device.
POST    /devices/messages/sms                                      Sends an SMS message to the device.
POST    /devices/messages/{id}/message                             Sends a message to the device.
POST    /devices/model-details                                     Load device model details for given device manufacturers.
GET     /devices/network                                           Returns network information of single device from alternate 
GET     /devices/networkinfosearch                                 Finds device network information matching specified criteria
GET     /devices/notes                                             Gets DeviceNotes by AlternateId.
POST    /devices/notes                                             Creates DeviceNotes by AlternateId.
GET     /devices/notes/{noteid}                                    Gets DeviceNotes by AlternateId.
PUT     /devices/notes/{noteid}                                    Updates a note identified by note ID for the device identifi
DELETE  /devices/notes/{noteid}                                    Deletes a note for the device identified by alternate ID.
GET     /devices/profiles                                          Retrieves the profile details of the device identified by al
GET     /devices/search                                            Find relevant devices using various criteria.
GET     /devices/security                                          Retrieves the security information of the device identified 
GET     /devices/securityinfosearch                                Searches for Device Security Information for the device.
PUT     /devices/serialnumber/{serialnumber}/customattributes      Update device custom attributes by serial number.
DELETE  /devices/serialnumber/{serialnumber}/customattributes      Delete device custom attributes by serial number.
POST    /devices/serialnumber/{serialnumber}/sendmessage           Sends a push notification to the device identified by serial
POST    /devices/smartgroups                                       Query the smart groups for devices.
POST    /devices/smartgroups/device-map-diagnostics                Query smart groups for devices using device uuid.
GET     /devices/udid/{udid}                                       Get device info based on UDID.
GET     /devices/udid/{udid}/deviceenrollmentstatus                Retrieves Device status based on the device identifier(UDID)
GET     /devices/user                                              Retrieves the user details of the device identified by the a
GET     /devices/workflows/{workflowUuid}/status/device-count      Get the count of devices for each workflow status.
POST    /devices/{deviceId}/commands/finddevice                    Executes find device command for device by device id.
GET     /devices/{deviceId}/compliance                             Retrieves compliance details of the device identified by dev
GET     /devices/{deviceId}/loggedinusers                          Gets all logged in users on the device.
PUT     /devices/{deviceId}/maintenance                            Updates the maintenance mode of a device.
POST    /devices/{deviceId}/notes                                  Creates a new note for the device identified by device ID.
GET     /devices/{deviceId}/notes/{noteId}                         Retrieves a particular note identified by note ID for the de
PUT     /devices/{deviceId}/notes/{noteId}                         Updates a note identified by note ID for the device identifi
DELETE  /devices/{deviceId}/notes/{noteId}                         Deletes a note identified by note ID for the device identifi
GET     /devices/{deviceType}/manufacturers                        Get the associated Manufacturers (OEM) by platforms.
GET     /devices/{deviceUuid}/apps                                 Returns details for the specified app installed/assigned to 
GET     /devices/{deviceUuid}/apps/search                          Returns the apps which are applicable to the device.
GET     /devices/{deviceUuid}/bitlocker/drives                     Returns the Bit Locker drive level information for the devic
GET     /devices/{deviceUuid}/bitlocker/drives/{volumeIdentifier}/protectors Returns the Bit Locker protector information for the device.
GET     /devices/{deviceUuid}/bitlocker/recoveryKey/{recoveryKeyID} Returns the decrypted Bit Locker recovery key using the reco
PUT     /devices/{deviceUuid}/commands/change-organization-group/{organizationGroupUuid} Changes the organization group to which the device is assign
GET     /devices/{deviceUuid}/conditional-access-device-registration-information Retrieve Conditional Access device registration information
GET     /devices/{deviceUuid}/eventactions                         Gets Event Actions by Device UUID.
POST    /devices/{deviceUuid}/execute/genericcommand               Triggers "Execute generic command" for the specified device.
GET     /devices/{deviceUuid}/filesactions                         Gets FilesActions by Device UUID.
POST    /devices/{deviceUuid}/launcher/enter-admin-mode            Executes the command ExitAdminMode for a device by the devic
POST    /devices/{deviceUuid}/launcher/exit                        Executes the command ExitAirWatchLauncher for a device by th
POST    /devices/{deviceUuid}/launcher/exit-admin-mode             Executes the command ExitAdminMode for a device by the devic
PUT     /devices/{deviceUuid}/lostmode/{enableLostMode}            Updates the lost mode of device.
GET     /devices/{deviceUuid}/sensors                              Returns the list of sensors reported by the specified device
GET     /devices/{deviceUuid}/sources/{sourceUuid}/logs            Gets the logs associated with particular source from device.
GET     /devices/{deviceUuid}/tunnel/discovery                     Retrieves the endpoint details to fetch tunnel configuration
GET     /devices/{deviceUuid}/tunnel/profile                       Retrieves the tunnel configuration for the device.
GET     /devices/{deviceUuid}/workflows/status                     Get the workflow status for device.
GET     /devices/{deviceUuid}/workflows/{workflowUuid}/status      Gets the status of the workflow and corresponding steps for 
POST    /devices/{device_uuid}/tags/{tag_uuid}                     Associates tag with a device
DELETE  /devices/{device_uuid}/tags/{tag_uuid}                     Dissociate tag from a device
POST    /devices/{deviceid}/commands                               Executes commands for the specified device.
POST    /devices/{deviceid}/commands/appdeploymentlog              Requests WinRT device to upload the last deployment log of a
POST    /devices/{deviceid}/commands/startairplay                  Executes start airplay for a specific device.
GET     /devices/{id}                                              Get Device details by Device id.
PUT     /devices/{id}                                              Edit the device details identified by Device id.
DELETE  /devices/{id}                                              Delete Device details by Device id.
GET     /devices/{id}/adminapps                                    Retrieves Admin applications details for passed DeviceID.
GET     /devices/{id}/apps                                         Retrieves application details of the device identified by de
GET     /devices/{id}/certificates                                 Retrieves certificate details of the device identified by de
PUT     /devices/{id}/commands/changeorganizationgroup/{organizationgroupid} Changes the organization group to which the device is assign
POST    /devices/{id}/commands/changepasscode                      Executes change passcode command for device ID.
POST    /devices/{id}/commands/installprofile                      Installs the profile on device.
GET     /devices/{id}/content                                      Retrieves the content details of the device identified by de
PUT     /devices/{id}/customattributes                             Update device custom attributes by device identifier.
DELETE  /devices/{id}/customattributes                             Delete device custom attributes by device identifier.
GET     /devices/{id}/eventlog                                     Retrieves events corresponding to the device identified by d
GET     /devices/{id}/gps                                          Retrieves the GPS coordinates of the device identified by de
POST    /devices/{id}/messages/email                               Sends an email to the user of the device.
POST    /devices/{id}/messages/push                                Sends a push message to the device.
POST    /devices/{id}/messages/sms                                 Sends an SMS message to the device.
GET     /devices/{id}/network                                      Returns network information of single device specified by id
GET     /devices/{id}/notes                                        Retrieves the notes for the device identified by device ID.
GET     /devices/{id}/profiles                                     Retrieves the profile details of the device by Device ID.
GET     /devices/{id}/security                                     Retrieves the security information of the device identified 
GET     /devices/{id}/smartgroups                                  Retrieves all the smart groups associated with the device.
GET     /devices/{id}/user                                         Retrieves the user details of the device identified by devic
GET     /devices/{uuid}/deem/{resourceUuid}                        Get DEEM Configuration for a device
GET     /devices/{uuid}/security/encryption-status                 Get encryption status of an enrolled device.
GET     /devices/{uuid}/security/managed-admin-information         Get information of the administrator account configured on a
GET     /devices/{uuid}/security/recovery-lock-password            Gets the Recovery Lock password for a macOS device.
GET     /devices/{uuid}/tags                                       Retrieves associated tags for a device
POST    /devices/{uuid}/tunnel/{tunnelConfigUuid}/applications/{bundleId}/profile/{profileUuid}/scep-token/{issuer} Generates one time token to allow device to obtain authentic
POST    /devicesensors                                             Create a device sensor.
POST    /devicesensors/assign                                      Assign device sensors to smart groups.
POST    /devicesensors/bulkdelete                                  Deletes the list of device sensors based on the identifiers 
GET     /devicesensors/list/{organizationGroupUuid}                Gets the list of all the device sensors for the Organization
GET     /devicesensors/{sensorUuid}                                Gets the device sensor information.
PUT     /devicesensors/{sensorUuid}                                Update the device sensor.
GET     /devicestatemetadata/{organizationgroupUuid}               Get device state attribute metadata for an Organization Grou
GET     /groups/{groupId}/assignmentgroups                         Returns a list of Assignment Groups matching the search crit
GET     /groups/{ogUuid}/enrollment-tokens                         Returns a list of enrollment tokens that match the search cr
POST    /groups/{ogUuid}/enrollment-tokens                         Creates device enrollment token based on registration type
GET     /groups/{ogUuid}/enrollment-tokens/{tokenUuid}             Get device enrollment token details
DELETE  /groups/{ogUuid}/enrollment-tokens/{tokenUuid}             Delete device enrollment token
GET     /groups/{ogUuid}/last-sync                                 Get OPS device last sync details for the given organization 
GET     /groups/{organizationGroupUuid}/google-chrome-os-organizational-units Returns a list of Organizational Units matching the search c
PATCH   /groups/{organizationGroupUuid}/phased-deployments/{phasedDeploymentUuid} Patch a phased deployment.
POST    /groups/{organizationGroupUuid}/phased-deployments/{phasedDeploymentUuid}/events Search phase events for a phased deployment.
PATCH   /groups/{organizationGroupUuid}/phased-deployments/{phasedDeploymentUuid}/phases/{phaseUuid} Patch a specific phase in a phased deployment.
GET     /groups/{organizationGroupUuid}/scripts                    GetScriptsByOrganizationGroupAsync
POST    /groups/{organizationGroupUuid}/scripts                    CreateScriptAsync
POST    /groups/{organizationGroupUuid}/scripts/bulkdelete         ScriptBulkDelete
POST    /groups/{organizationGroupUuid}/scripts/samples            Get script samples.
GET     /groups/{uuid}/device/{deviceUuid}/oemupdates              Gets all OEM Update Summary details of a given organization 
GET     /groups/{uuid}/oemupdates/summary                          Gets all OEM Update Summary details of a given organization 
GET     /groups/{uuid}/oemupdates/summary/search                   Returns a collection of OemUpdateSummary details based on th
GET     /groups/{uuid}/oemupdates/summary/{summaryUuid}/devices    Gets the devices where OEM Update Summary is installed(statu
GET     /groups/{uuid}/oemupdates/summary/{summaryUuid}/status     Gets the count of OemUpdate Summary installed in devices for
GET     /groups/{uuid}/settings/directenrollment                   Get direct enrollment settings for Workspace ONE
POST    /groups/{uuid}/settings/directenrollment                   Creates a new system override to save direct enrollment sett
POST    /tags/addtag                                               Add a new tag.
GET     /tags/search                                               Retrieve the list of tags based off name, organization group
DELETE  /tags/{tagId}                                              Delete a tag.
GET     /tags/{tagId}/devices                                      Retrieves all the devices with the specified tag.
POST    /tags/{tagId}/update                                       Updates a tag name, tag type or tag avatar.
POST    /tags/{tagid}/adddevices                                   Add devices to the tag.
POST    /tags/{tagid}/removedevices                                Remove devices from the tag.
```

### v2 — 32 operações de device/tag/grupo

```
POST    /devices/apps                                              Returns list of application(s) for provided devices.
POST    /devices/commands/{commandName}                            Executes command for multiple devices identified by device u
POST    /devices/commands/{commandName}/device/{searchBy}/{id}     Executes a command for device by alternate ID
GET     /devices/devicecountinfo                                   Retrieves Device Count Information which are Categorized by 
GET     /devices/search                                            Find relevant devices using various criteria.
POST    /devices/{deviceUuid}/commands/{commandName}               Executes a command for device by device uuid
GET     /devices/{deviceUuid}/conditional-access-device-registration-information Retrieve Conditional Access device registration information
POST    /devices/{deviceUuid}/messages/email                       Sends an email to the user of the device.
POST    /devices/{deviceUuid}/messages/sms                         Sends an SMS message to the device.
PATCH   /devices/{id}/enrollmentuser/{enrollmentuserid}            Check In and Check Out the device to the Multi-Staging enrol
GET     /devices/{uuid}                                            Get basic details about the device
GET     /devices/{uuid}/installs                                   Retrieve application install status for an AFW device
GET     /devices/{uuid}/osupdate                                   Retrieves available OS updates for a device
POST    /devicesensors                                             Create a device sensor.
GET     /devicesensors/assignments/{assignmentUuid}                Gets the device sensor assignment information.
PUT     /devicesensors/assignments/{assignmentUuid}                Update the device sensor assignment information.
DELETE  /devicesensors/assignments/{assignmentUuid}                Deletes the device sensor assignment.
GET     /devicesensors/list/{organizationGroupUuid}                Gets the list of all the device sensors for the Organization
GET     /devicesensors/{sensorUuid}                                Gets the device sensor information.
PUT     /devicesensors/{sensorUuid}                                Update the device sensor.
POST    /devicesensors/{sensorUuid}/assignment                     Adds an assignment to device sensor.
GET     /devicesensors/{sensorUuid}/assignments                    Gets the list of device sensor assignments.
POST    /devicesensors/{sensorUuid}/assignments                    Bulk update device sensor assignments based on custom action
POST    /groups/{ogUuid}/enrollment-tokens                         Creates device enrollment token based on registration type
GET     /groups/{organizationGroupUuid}/assignmentgroups           Returns a list of Assignment Groups matching the search crit
GET     /groups/{organizationGroupUuid}/picklists/certificate-authorities Gets the list of Certificate Authorities (CA) for an organiz
GET     /tags/search                                               Returns a list of Tags for a given organization group.
PUT     /tags/{tagUuid}                                            Updates the tag.
DELETE  /tags/{tagUuid}                                            Deletes the tag.
GET     /tags/{tagUuid}/devices                                    Returns a list of devices for a given tag uuid or lastseen.
POST    /tags/{tagUuid}/devices                                    Add devices to the tag.
DELETE  /tags/{tagUuid}/devices                                    Remove devices from the tag.
```

### v3 — 8 operações de device/tag/grupo

```
POST    /devices/action                                            Execute actions on devices.
POST    /devices/commands/{commandName}/device/{searchBy}/{id}     Executes a command for device by alternate identifier.
GET     /devices/search                                            Searches the device using the query information provided.
POST    /devices/{deviceUuid}/commands/{commandName}               Executes a command for a device by the device uuid.
POST    /devices/{deviceUuid}/profiles/{profileUuid}               Uploads a completed (already built for device) profile
GET     /devices/{uuid}                                            Get basic details about the device.
GET     /groups/{organizationGroupUuid}/profiles                   Get all device profiles as per search filter
POST    /tags                                                      Creates a New Tag.
```

### v4 — nenhuma

O `mdmv4.json` não publica caminho de device nem de tag: o único `path` do
arquivo é `GET /profiles/{profileId}`. O que ele traz são os **modelos**
(`DeviceActionRequestV4Model`, `DeviceBulkActionRequestV4Model`,
`ManageTagsRequestModelV4`, `DeviceActionSearchRequestModelV4`,
`DeleteDeviceRequestModelV4`). Para descobrir os caminhos da V4 nesta
instalação, é o `--descobrir` contra `/api/help/`.
