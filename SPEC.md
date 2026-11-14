# photo-origin-tag: especificação (07/11/2026; atualizada em 15/11/2026, v0.8.0)

Carimba nos TIFFs exportados pelo Capture One **de onde a foto veio**
(RAW, JPEG, TIFF 8-bit ou TIFF 16-bit), antes do jxl-photo transformá-los em
master. A regra nasceu do fluxo do dono (C1, NX Studio, OM Workspace, layout
`<câmera>\_EXPORT\TIFF16`). Repo público desde 12/11/2026: nada pessoal nos
arquivos versionados (nomes reais de pastas, pessoas, lugares). O dono roda a
cópia em `..\photo-origin-tag_LOCAL` (§5).

## 1. O que é gravado no TIFF

| Origem detectada | `XMP-dc:Relation` | `EXIF XPComment` (Windows: "Comentários") |
|---|---|---|
| RAW | nada (o encoder já grava `tiff16`) | `Source: RAW` |
| TIFF 16-bit | nada (idem) | `Source: TIFF 16-bit` |
| TIFF 8-bit | `jxlphoto-origin:tiff8` | `Source: TIFF 8-bit` |
| JPEG | `jxlphoto-origin:jpeg` | `Source: JPEG` |

- (08/11, v0.3.0) Para original JPEG/TIFF, o comentário leva também o NOME do
  perfil de cor do original: `Source: JPEG, Adobe RGB (1998)`. Sem ICC, vale a
  tag EXIF ColorSpace (`sRGB (EXIF, no ICC)`, `Adobe RGB (EXIF, no ICC)` =
  ColorSpace 2 ou Uncalibrated + R03); sem nenhum dos dois, `no color
  profile`; original ilegível = só o tipo (nunca chuta). RAW não leva perfil
  (o export tem o perfil que o C1 escolheu). Só no XPComment, que o jxl-photo
  não lê; nunca o ICC em base64.
- O vocabulário de `jxlphoto-origin` é o do jxl-photo, que não tem `raw`.
  Por isso o RAW fica `tiff16` no token e só o comentário diz RAW.
- O encoder herda um `jxlphoto-origin` já gravado no TIFF
  (`_origin_for_output`, teste real `test_r2a`), sem mudar nada no jxl-photo.
- Nenhum token `jxlphoto-*` novo: o namespace é do jxl-photo, e um token
  desconhecido faria o encoder tratar o TIFF como "já tocado pelo toolkit".
- A data do arquivo NÃO é preservada. O arquivo mudou, então o sync do encoder
  refaz o master e o backup copia a versão nova.

## 2. Regra de detecção (por export)

1. Entra arquivo `.tif/.tiff` dentro de `<base>\_EXPORT\...` e, desde a v0.4.0,
   JPEG dentro de `<base>\_EXPORT\JPG_only\...` (§7). O original é procurado em
   `<base>`. Os nomes das pastas são constantes no topo do script. As raízes
   passadas na linha de comando viram caminho absoluto (`os.path.abspath`, não
   `resolve()`, que segue junctions e troca a letra do disco por UNC, e os
   caminhos do `overrides.csv` deixariam de casar).
   - (12/11, v0.7.0) A raiz pode ser o disco inteiro (`G:\`): a lixeira e o
     `System Volume Information` nunca são abertos (`SKIP_DIRS`), e os nomes
     são filtrados antes de virar `Path` (o `G:` tem 1,4 milhão de arquivos;
     a varredura leva ~40 s, ~4 s numa segunda passada). É o que a tarefa
     agendada passa: o mesmo escopo do SYNC PHOTOS e do JPG ONLY do jxl-photo,
     que varrem o `G:\` inteiro. Uma lista de anos (`G:\2024 G:\2025
     G:\2026`) deixava 2027 de fora e, com um ano que ainda não existe, parava
     o dia (código 2).
   - (15/11) As pastas `CaptureOne` (configurações e cache do Capture One,
     onde nunca há export) também nunca são abertas (`SKIP_DIRS`). A
     varredura segue junctions, e um cache movido para outro disco é uma
     junction `CaptureOne\Cache`: no G: eram 793 mil dos 1,36 milhão de nomes
     listados, nenhum export; a varredura fria caiu de 72 s para 51 s.
2. **O original ao lado decide:** o arquivo de `<base>` cujo nome é o MAIOR
   prefixo do nome do export (`DSC_1211_NXstudio_ProPhoto-g22_v1.tif` →
   `DSC_1211_NXstudio.*`).
   - só RAW → RAW
   - só JPEG → JPEG
   - só TIFF → lê a profundidade do TIFF original (`tiff8` ou `tiff16`)
3. **Par RAW+JPEG da câmera:**
   - NEF: `XMP-crd` ou `ExifIFD:SubjectDistance` no export → RAW (validado:
     100% de acordo com os labels em 2024). **Sem o sinal só vale como JPEG
     com label verde**, em TIFF e em JPEG (decidido em 11/11: a falta de sinal
     não prova nada, o recipe WEB do C1 o tira); sem o verde vai para revisão.
     Nos dados reais os 752 casos tinham verde.
   - O sinal não vem só do NEF: o JPEG que o NX Studio faz do NEF herda o
     `XMP-crd` (14 casos reais) e o C1 o copia para o export. Por isso, quando
     o export tem o sinal, sem override e sem label azul, lê-se também o JPEG
     ao lado: se ele carrega o sinal, o sinal do export não vale e decide o
     label (verde = JPEG; qualquer outro = revisão). JPEG ilegível conta como
     sem o sinal.
   - Outra marca (Fuji RAF, ...): não há sinal na máquina → decide o label do
     C1 (azul = RAW, verde = JPEG). O relatório marca "decidido pelo label".
4. **Par RAW+TIFF (Olympus: ORF + TIF do OM Workspace):** decide o label
   (azul = RAW, verde = profundidade do TIF).
4b. **Trio RAW+JPEG+TIFF** (os `DSCF` RAF+JPG+TIF da S5Pro): revisão por
   padrão. Com `--trio-blue-raw` (v0.6.0, configuração `TRIO_BLUE_IS_RAW`,
   desligada no script, ligada pelo dono nos presets em 11/11): label azul =
   RAW (trio de NEF precisa também do sinal); qualquer outro label = revisão
   (o verde não diz se veio do JPEG ou do TIFF). Simulação de 11/11 no
   G:\2024-2026: os 18 trios reais são todos `DSCF` azuis.
5. **Overrides** (`overrides.csv`: caminho de arquivo ou de pasta → origem,
   com observação) vencem tudo. É onde ficam as conferências manuais do dono.
6. **Nunca chuta.** Fica sem carimbo e vai para a lista de revisão: sem
   original ao lado, combinação não prevista, label contradizendo um sinal de
   máquina sem override, label ausente quando a regra precisa dele.

## 3. Quando NÃO escrever

- O TIFF já tem algum token `jxlphoto-*` (decodificado pelo jxl-photo, não é
  export do C1) → pula.
- O TIFF já tem a MESMA origem → nada a fazer (idempotente).
- O TIFF já tem OUTRA origem → recusa. Só um override troca.
- O `XPComment` já tem um texto que não é exatamente um comentário deste
  script → recusa o arquivo, com ou sem override (nunca apaga um comentário do
  dono, nem um que comece com `Source: `).
- O `XPComment` deste script com o MESMO tipo e outro perfil (ou sem perfil,
  da v0.2) → reescreve sem override: o perfil é lido do original, não é regra.
  Mudar o texto de um perfil (`PROFILE_*`) ou um exiftool que leia o nome de
  outro jeito reescreve TODOS os comentários que o levam, e o SYNC refaz os
  masters deles. Por isso (v0.7.0) `--max-rewrites N`: com `--apply`, mais de
  N escritas que mudam tags de uma execução anterior (comentário deste script
  trocado, token removido) → nada é gravado, código 4, e o relatório diz o que
  cada uma troca (`replaces ...`, `removes ...`). Export novo nunca conta.
- Arquivo modificado entre a leitura e a escrita, travado ou em uso → recusa.

## 4. Segurança da escrita

- O padrão é **simulação**. `--apply` grava.
- exiftool só via argfile com `-charset filename=utf8` (caminhos em japonês,
  `[ ]`, `%`, mais de 260 caracteres).
- Antes e depois da escrita, em cada arquivo:
  - hash dos bytes da imagem (strips/tiles lidos sem descomprimir) → idêntico;
  - perfil ICC (bytes) → idêntico;
  - todas as tags (`exiftool -j -G1 -a`) → idênticas, exceto as duas escritas.
  Se algo diferir, o original fica como estava e o arquivo é marcado ERRO.
- Não há backup nem restauração: o exiftool escreve uma **cópia temporária**
  na mesma pasta (`<nome>~origin-tag-tmp-<pid><ext>`), a cópia é conferida
  (bytes da imagem, ICC e todas as outras tags), confere-se que o original não
  mudou desde a leitura, e só então `os.replace` troca o original pela cópia.
- Caminho com `%`: o `-o` do exiftool lê `%d %f %e %c` (com modificadores) no
  nome de saída como códigos de formato, e `%%` não é escape (exiftool 13.59):
  o arquivo dá erro e sobra uma cópia numa árvore de pastas nova. Para esses
  caminhos o Python faz a cópia e o exiftool a edita no lugar
  (`-overwrite_original`; o temporário do próprio exiftool,
  `<cópia>_exiftool_tmp`, também é limpo). Sem `%` segue o `-o`, o caminho de
  todos os arquivos reais.
- Códigos de saída: 0 = ok; 1 = algum arquivo deu erro e ficou como estava;
  2 = uso/configuração, nada rodou (raiz inexistente, `overrides.csv`
  inválido, `--overrides` que não existe, `--workers`/`--max-rewrites`
  inválido, falta `tifffile`, exiftool ausente ou que não responde, outro
  `--apply` rodando); 3 = execução abortada (exceção, Ctrl+C); 4 =
  `--max-rewrites` estourado, nada gravado. O relatório CSV é gravado sempre
  que o planejamento terminou, inclusive nos abortos (o que não foi alcançado
  fica `write`, motivo `not attempted`). A tarefa agendada para em 2 ou mais.
- (v0.7.0) Toda execução começa com `exiftool -ver` (versão na linha de
  cabeçalho). Sem isso, um exiftool ausente ou quebrado fazia todo export
  "ilegível": código 1, e a tarefa seguia arquivando JPEG sem comentário.
- (v0.7.0) Um `--apply` por vez: trava no `Logs\photo_origin_tag.lock`
  (`msvcrt.locking` num byte depois do texto, como as travas do jxl-photo). Quem
  segura é o processo vivo; um processo morto não deixa trava. O segundo
  `--apply` sai com 2 antes de limpar temporários (senão apagaria os do
  primeiro). A simulação não usa a trava.
- (v0.7.0) O resumo vem ANTES do relatório. Relatório que não pode ser gravado
  onde o `--report` diz (aberto no Excel) vai para `Logs\` com um aviso; antes
  isso virava código 3 depois de gravar os arquivos, e a tarefa parava o dia.
- Um export que o exiftool não consegue ler como o tipo da extensão (vazio,
  1 byte, JPEG com extensão `.tif`) é `error` já na simulação.
- Relatório CSV + log por execução: cada arquivo com origem, como foi
  decidido, ação (carimbado / já estava / recusado / revisar) e motivo.
- Progresso na tela (N/total, tempo restante) nas fases longas.
- (15/11) Leituras do planejamento: enquanto o exiftool lê um lote, uma
  thread abre (e fecha) os próximos arquivos, no máximo `OPEN_AHEAD` (400)
  à frente do que ele já respondeu; 0 desliga. O antivírus de acesso varre
  cada arquivo na primeira abertura (~19 ms e ~350 KB lidos por arquivo frio
  no HD do dono), mais do que o trabalho do exiftool (5–11 ms); aberto antes,
  os dois se sobrepõem. Não lê dados nem muda resposta nenhuma: quem lê cada
  arquivo continua sendo o exiftool, e um arquivo que a thread não consegue
  abrir fica para ele, como antes.

## 5. Uso

- Agora: rodar em todo o G: (2024 → 2026), conferir a lista de revisão,
  depois `--apply`, depois o REBUILD dos masters.
- Onde roda (12/11): `..\photo-origin-tag_LOCAL`, sem git, como o
  `jxl-photo_LOCAL` e o `tiff-workflow_LOCAL`. Lá ficam cópias idênticas dos
  dois `.py` do repo, o `overrides.csv` real do dono, os `Logs\` e o estado do
  checklist (`Logs\pastas_exportar_tiff.state.json`). A tarefa agendada, o
  `JXL 9` e o `EXPORT - FALTA TIFF.bat` chamam a cópia de lá. O
  `overrides.csv` do repo é só o modelo vazio.
- Daqui pra frente: um passo antes do SYNC PHOTOS na tarefa agendada
  (`jxl-photo_LOCAL\run_scheduled_presets.cmd`, desde 11/11): `--apply
  --trio-blue-raw`; código ≥ 2 = o tagger não rodou, foi abortado ou recusou
  uma reescrita em massa, e nada mais roda nesse dia (nenhum JPEG é arquivado
  sem o comentário). Com a v0.7.0: raiz `G:\` (§2.1), `--max-rewrites 50`, e
  a saída vai para `jxl-photo_LOCAL\Logs\photo_origin_tag_scheduled.log`.
- Nenhum preset muda: o SRGB já roda com `--on-regeneration convert`, e o
  JPG MOBILE é o transcoder (JXL → JPEG), que não tem essa trava.
- Decidido 07/11: grava SÓ nos TIFFs e refaz masters + cópias (sem editar
  masters). O `.bat` `presets_LOCAL\JXL 9 - ORIGEM + MASTERS + COPIAS.bat`
  faz tudo, depois de um backup.

## 6. Testes (antes de tocar no G:)

- pytest com exiftool real, todos em pasta com japonês + `[ ]` + espaço + `%`:
  cada linha da regra, cada recusa, a idempotência, o rollback quando a
  verificação falha, um TIFF comprimido em ZIP (tiff-workflow) e um sem
  compressão.
- Ponta a ponta: carimbar → `jxl_tiff_encoder.py` → o master tem
  `jxlphoto-origin:jpeg`, e o Explorer mostra o comentário (XPComment no Exif
  box do JXL).
- Simulação em cópias de `E:\TESTE`, depois simulação no G: inteiro
  comparada com a varredura de 07/11 (2024: 532 jpeg; 2025: 1270 jpeg + 7
  tiff8; 2026: 181 jpeg).

## 7. JPEGs de `_EXPORT\JPG_only` (09/11/2026, v0.4.0)

Fotos sem TIFF possível. Só o `XPComment`, sem XMP e sem `jxlphoto-origin`.

- Tem que rodar ANTES do preset JPG ONLY: depois do archive, o JPEG é recusado,
  porque mudaria em relação ao que o JXL devolve.
- A escrita confere que todo byte fora do segmento Exif continua igual.
- JPEG sem nenhum Exif (o recipe WEB às vezes tira tudo): o exiftool cria o
  IFD0 com `XResolution`, `YResolution`, `ResolutionUnit` e
  `YCbCrPositioning`; só nesse caso essas quatro tags novas são aceitas pela
  conferência. Em JPEG que já tem Exif, mudar qualquer uma delas continua
  sendo erro.
- Par NEF+JPG: sem sinal de RAW, só vale como JPEG com label verde (desde a
  v0.5.0 vale também para TIFF, §2.3). O recipe WEB
  do C1 tira o `XMP-crd` e às vezes todo o EXIF (medido em 09/11: 70 cópias WEB
  da Zf davam "JPEG" com as mesmas fotos em AdobeRGB dando RAW). Sem o label vai
  para revisão.
- Conferido em cópias dos 346 JPEGs reais (09/11): 214 marcados, 214 voltam bit
  a bit pelo transcoder do jxl-photo, com o comentário no JXL.

## Em aberto

- Texto do comentário: DECIDIDO em inglês (`Source: JPEG`), 07/11.
- Conferências manuais pendentes: na lista de pendências do dono (fora do
  repo).
