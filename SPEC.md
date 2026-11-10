# photo-origin-tag: especificação (07/11/2026; atualizada em 11/11/2026, v0.5.0)

Carimba nos TIFFs exportados pelo Capture One **de onde a foto veio**
(RAW, JPEG, TIFF 8-bit ou TIFF 16-bit), antes do jxl-photo transformá-los em
master. Repo privado: a regra depende do fluxo do dono (C1, NX Studio,
OM Workspace, layout `<câmera>\_EXPORT\TIFF16`).

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
  2 = uso/configuração (raiz inexistente, `overrides.csv` inválido,
  `--workers`, falta `tifffile`); 3 = execução abortada (exceção, Ctrl+C).
  O relatório CSV é gravado sempre que o planejamento terminou, inclusive nos
  abortos (o que não foi alcançado fica `write`, motivo `not attempted`).
  A tarefa agendada (se um dia existir) deve parar em 2 ou mais.
- Um export que o exiftool não consegue ler como o tipo da extensão (vazio,
  1 byte, JPEG com extensão `.tif`) é `error` já na simulação.
- Relatório CSV + log por execução: cada arquivo com origem, como foi
  decidido, ação (carimbado / já estava / recusado / revisar) e motivo.
- Progresso na tela (N/total, tempo restante) nas fases longas.

## 5. Uso

- Agora: rodar em todo o G: (2024 → 2026), conferir a lista de revisão,
  depois `--apply`, depois o REBUILD dos masters.
- Daqui pra frente: um passo antes do SYNC (um `.bat` nos presets), rodando
  só nas pastas novas.
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
