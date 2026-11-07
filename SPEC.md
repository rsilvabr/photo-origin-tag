# photo-origin-tag: especificação (rascunho 1, 07/11/2026)

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

- O vocabulário de `jxlphoto-origin` é o do jxl-photo, que não tem `raw`.
  Por isso o RAW fica `tiff16` no token e só o comentário diz RAW.
- O encoder herda um `jxlphoto-origin` já gravado no TIFF
  (`_origin_for_output`, teste real `test_r2a`), sem mudar nada no jxl-photo.
- Nenhum token `jxlphoto-*` novo: o namespace é do jxl-photo, e um token
  desconhecido faria o encoder tratar o TIFF como "já tocado pelo toolkit".
- A data do arquivo NÃO é preservada. O arquivo mudou, então o sync do encoder
  refaz o master e o backup copia a versão nova.

## 2. Regra de detecção (por export)

1. Só entra arquivo `.tif/.tiff` dentro de `<base>\_EXPORT\...`. O original é
   procurado em `<base>`. Os nomes das pastas são constantes no topo do script.
2. **O original ao lado decide:** o arquivo de `<base>` cujo nome é o MAIOR
   prefixo do nome do export (`DSC_1211_NXstudio_ProPhoto-g22_v1.tif` →
   `DSC_1211_NXstudio.*`).
   - só RAW → RAW
   - só JPEG → JPEG
   - só TIFF → lê a profundidade do TIFF original (`tiff8` ou `tiff16`)
3. **Par RAW+JPEG da câmera:**
   - NEF: `XMP-crd` ou `ExifIFD:SubjectDistance` no export → RAW, senão JPEG
     (validado: 100% de acordo com os labels em 2024).
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
- O `XPComment` já tem um texto que não é deste script → recusa o arquivo
  (nunca apaga um comentário do dono).
- Arquivo modificado entre a leitura e a escrita, travado ou em uso → recusa.

## 4. Segurança da escrita

- O padrão é **simulação**. `--apply` grava.
- exiftool só via argfile com `-charset filename=utf8` (caminhos em japonês,
  `[ ]`, `%`, mais de 260 caracteres).
- Antes e depois da escrita, em cada arquivo:
  - hash dos bytes da imagem (strips/tiles lidos sem descomprimir) → idêntico;
  - perfil ICC (bytes) → idêntico;
  - todas as tags (`exiftool -j -G1 -a`) → idênticas, exceto as duas escritas.
  Se algo diferir, restaura o original (backup temporário na mesma pasta) e
  marca ERRO.
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

## Em aberto

- Texto do comentário: DECIDIDO em inglês (`Source: JPEG`), 07/11.
- Conferências manuais pendentes: na lista de pendências do dono (fora do
  repo).
