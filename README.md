# TechDiag - Diagnóstico e Laudo Técnico para Windows

Aplicativo desktop (Python 3 + PySide6) para técnicos de informática: coleta hardware, saúde de discos e bateria,
RAM, drivers, rede, telas azuis, reinicializações e logs do Windows; sugere ações e upgrades, pesquisa preços e
gera um laudo técnico em PDF. Funciona **somente no Windows** (em outros sistemas o código abre, mas cada
verificação informa claramente que está indisponível).

## Instalação e execução (Windows)

```powershell
scripts\install.ps1     # cria .venv e instala dependências
scripts\run.ps1         # executa a partir do código-fonte
scripts\test.ps1        # testes automatizados
scripts\build.ps1       # roda os testes e gera dist\TechDiag.exe (PyInstaller)
```

## Ferramentas externas (pasta `tools\`)

Copie os executáveis (não redistribuídos neste projeto, por licença): `RAMMap64.exe` (Sysinternals),
`BlueScreenView.exe` (NirSoft), `speedtest.exe` (Ookla Speedtest CLI) e `HWiNFO64.exe` (somente sensores). O código as localiza em `tools\` ao lado do
`.exe`, em `resources\tools\` e na pasta embutida pelo PyInstaller. Cada uso valida existência, registra SHA-256 e a
assinatura digital (Authenticode); em Configurações é possível **bloquear ferramentas sem assinatura válida**
(BlueScreenView costuma ser distribuído sem assinatura). Sem a ferramenta, a verificação continua com fallback
(ex.: telas azuis via evento 1001) e informa o motivo.

## Como cada verificação funciona

| Verificação | Fonte |
|---|---|
| Hardware | CIM/WMI via PowerShell (Win32_Processor, PhysicalMemory, BaseBoard, BIOS, VideoController, WmiMonitorID...) |
| Armazenamento | Get-PhysicalDisk, Get-StorageReliabilityCounter, atributos SMART ATA e PredictFailure (root\wmi, exigem admin). Indicador indisponível aparece como "Não disponível", nunca estimado |
| Bateria | `powercfg /batteryreport /xml`; desgaste = (1 - Full/Design) x 100 |
| RAM | psutil + contadores `Win32_PerfRawData_PerfOS_Memory` (independentes de idioma) + histórico do Windows Memory Diagnostic (eventos 1101/1102). RAMMap é aberto pelo botão; `mdsched.exe` é aberto sem reiniciar o PC |
| Temporários | %TEMP%, %LOCALAPPDATA%\Temp e C:\Windows\Temp; duplicados não são contados duas vezes; links/junctions nunca são seguidos |
| Drivers | Win32_PnPEntity (códigos de erro), Win32_PnPSignedDriver (versão, data, assinatura) |
| Rede | Get-NetAdapter, netsh wlan, Test-Connection; Speedtest CLI somente com consentimento |
| Telas azuis | BlueScreenView (`/scomma`) ou eventos 1001; interpretação como indício, não causa |
| Reinicializações | Eventos 41, 6005, 6006, 6008, 1074 e Kernel-General 12/13, com linha do tempo |
| Logs | System, Application, Setup e Windows Error Reporting, agrupados por fonte/ID com base de conhecimento (importante x comum) |
| Integridade | DISM /CheckHealth (e SFC opcional); exige administrador |

## Linha de corte de upgrade (arquivo `services/upgrade_analysis.py`)

| Situação | Recomendação |
|---|---|
| Intel Core até a 6ª geração (ou anterior ao Core i) / AMD pré-Ryzen, **notebook** | Substituir o notebook (não gera orçamento de RAM/SSD/bateria) |
| Mesmo caso, **desktop** | Trocar o processador (exige validação: nessa geração costuma exigir placa-mãe e memória) |
| HDD mecânico | Upgrade para SSD |
| SSD com vida útil restante < 50% | Substituir o SSD |
| RAM instalada < 16 GB (soma dos módulos) | Ampliar para 16 GB, respeitando slots e capacidade máxima da placa |

As constantes (`CUTOFF_INTEL_GEN`, `MIN_RAM_GB`, `MIN_SSD_LIFE_PCT`) ficam no topo do arquivo. Notebook x desktop vem do
tipo de chassi da BIOS (Win32_SystemEnclosure).

## Temperaturas (HWiNFO) e limite oficial do processador

O HWiNFO é lido pela memória compartilhada documentada pelo fabricante (HWiNFO_SENS_SM2). Exige **administrador**. Se o HWiNFO
não estiver aberto, o programa o inicia minimizado, lê os sensores e o encerra. Se a memória compartilhada não for habilitada
automaticamente, abra o HWiNFO uma vez, ative *Shared Memory Support* nas configurações de sensores e rode de novo.
O limite oficial vem de `src/techdiag/data/cpu_thermal_limits.json` (com fonte e link): modelos AMD conferidos individualmente
e Intel Core 6ª a 14ª geração (Tjunction típico de 100 °C nas fichas ARK conferidas; a Intel informa que varia por produto).
Modelo fora da base **não recebe limite estimado**: a tela mostra "não identificado" e um link de busca na ficha oficial.
Para acrescentar modelos, copie o JSON para `%APPDATA%\TechDiag\cpu_thermal_limits.json`.
As margens de alerta (crítico ≤ 10 °C, atenção ≤ 25 °C do limite) são critério do TechDiag. Confirme a licença do HWiNFO para uso comercial.

## Limitações assumidas (leia antes de usar em campo)

- **Links de drivers**: fabricantes não têm API pública de busca por modelo. O botão "Baixar driver" abre uma busca
  restrita ao domínio oficial (Dell, HP, Lenovo, ASUS, Acer, Samsung, Intel, NVIDIA, AMD, Realtek...) e DriverPack
  (marcado como terceiro). Nenhuma versão "recomendada" é inventada: a tela informa "Verificar na página do
  fabricante". Nada é baixado ou instalado automaticamente.
- **Preços**: usa a API pública do Mercado Livre. Se ela exigir autenticação (comum), o app mostra o motivo e links de
  busca em Kabum, Pichau, Terabyte, Amazon e ML, sem preços inventados. Informe um token em Configurações para ativar.
- **Compatibilidade de upgrade**: a coleta não confirma slots M.2, formato de baia ou compatibilidade de bateria; esses
  itens saem marcados "requer validação técnica".
- **Temperaturas** dependem de sensores expostos pelo Windows/BIOS (zona térmica ACPI, contadores de confiabilidade).
- **Wi-Fi** usa `netsh`; os rótulos em PT-BR e EN são reconhecidos, outros idiomas podem vir parciais.
- **Análise de memória.dmp** depende do BlueScreenView e de permissão de leitura.
- Formato exato do XML do `powercfg` varia entre versões do Windows: o parser é tolerante, mas valide em um notebook real.

## Segurança

Sem alterações sem confirmação; limpeza restrita às três pastas Temp; nenhum driver instalado; sem reinício automático;
dados do cliente apenas no SQLite local (`%APPDATA%\TechDiag\history.db`); Speedtest só com consentimento; execução
de PowerShell via `-EncodedCommand`; ferramentas externas validadas (hash e assinatura).
Funciona sem administrador: verificações que exigem elevação indicam isso na tela e no laudo.

## Estrutura

```
src/techdiag/
  core/        modelos, orquestrador (threads), config, logs, privilégios
  infra/       PowerShell, assinatura digital, ferramentas externas, SQLite, HTTP
  collectors/  um módulo independente por verificação
  services/    ações corretivas, pesquisa de drivers/preços, análise de upgrade
  reports/     classificação geral e PDF (ReportLab)
  ui/          PySide6 (14 áreas de navegação)
tests/         32+ testes automatizados (pytest)
```

## Checklist de validação em hardware real (não executável fora do Windows)

Marque após testar: notebook sem bateria · HDD · SSD SATA · NVMe · sem internet · sem administrador · driver ausente ·
arquivo temporário bloqueado · ferramenta externa ausente/corrompida · geração do PDF · histórico persistido ·
interface responsiva durante o diagnóstico completo · `TechDiag.exe` em Windows limpo.
