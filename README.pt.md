# Rato mãos-livres com eego (EOG + EMG)

[Français](README.md) | **Português**

Um rato controlado sem as mãos, com o amplificador eemagine eego EE-410 (8 canais bipolares) e os elétrodos de pressão XE-293:

- O movimento dos olhos (EOG) desloca o cursor.
- A atividade muscular da face (EMG) faz os cliques: cerrar os dentes = botão esquerdo, levantar as sobrancelhas = botão direito.

> As mensagens que os programas mostram no ecrã/terminal estão em francês. Este documento explica o que cada passo faz.

## Ficheiros

| Ficheiro | Função |
| --- | --- |
| `config.py` | Todos os parâmetros: atribuição de canais, frequência de amostragem, filtros, velocidade, duração dos gestos. A montagem dos elétrodos está descrita no início do ficheiro |
| `eego.py` | Interface ctypes para a DLL do eego SDK 1.3.19, para ler os dados diretamente em Python |
| `source.py` | Fontes de dados: o amplificador real `EegoSource` e o simulador por teclado `SimSource` |
| `dsp.py` | Filtragem em tempo real: EOG com passa-baixo de 10 Hz; EMG com passa-banda de 30–200 Hz e envelope RMS; filtro notch para 50 Hz e harmónicos (100/150/200 Hz) |
| `check_device.py` | Autoteste do aparelho: modelo, firmware, tipo de canais, impedância, qualidade do sinal, amostras perdidas |
| `live_view.py` | Visualização do sinal em tempo real, para verificar os elétrodos; também pode gravar os dados |
| `calibrate.py` + `calibration.py` | Calibração guiada em ecrã inteiro (cerca de 1,5 min), gera `calibration.json` |
| `controller.py` | Lógica de controlo: EOG → cursor, EMG → gestos. Só cálculo, pode ser testado isoladamente |
| `mouse_control.py` | Programa principal |
| `test_pipeline.py` | Teste completo sem hardware (dados simulados: calibração, movimento, cliques, pausa, rejeição de piscadelas) |

Dependências: `pip install numpy scipy matplotlib` (o tkinter já vem com o Python).

---

## Passos com o aparelho

### 0. Preparar o SDK (só uma vez)

A pasta do SDK já descompactada que estava na pen USB **está corrompida**: a DLL de 64 bits tem só 352 KB (a versão completa tem 639 KB) e os ficheiros do driver também estão truncados.
É preciso descompactar de novo a partir do zip:

1. Crie a pasta `sdk` neste diretório e descompacte lá dentro o conteúdo de `eego-SDK-1.3.19.40453.zip` (o zip contém diretamente as pastas `eemagine`, `linux` e `windows`).
2. Confirme que existe `sdk\windows\64bit\eego-SDK.dll`, com cerca de 639 KB.

### 1. Instalar o driver (só uma vez)

1. Ligue o amplificador, abra o Gestor de Dispositivos e procure o dispositivo desconhecido com o ponto de exclamação amarelo.
2. Botão direito → Atualizar controlador → Procurar no computador → escolha `sdk\windows\driver\win8\x64`. No Windows 10/11 também se usa esta versão win8.
3. **No Windows 11 o driver é bloqueado** (o Gestor de Dispositivos mostra o código 39, "uma política de controlo de aplicações bloqueou este ficheiro"):
   este driver Cypress de 2017 usa a antiga assinatura cruzada (cross-signing), na qual as versões recentes do Windows 11 deixaram de confiar. Desligar a integridade da memória ou a lista de bloqueio de drivers **não resolve**.
   O que funciona é desativar temporariamente a imposição de assinatura de drivers em cada arranque: mantenha Shift e clique em "Reiniciar" → Resolução de problemas → Opções avançadas → Definições de arranque → Reiniciar → prima **7**.
   Só vale para esse arranque; depois de reiniciar é preciso repetir.

### 2. Autoteste do aparelho (sem elétrodos)

```
python check_device.py --no-impedance
```

Verifique três coisas:

- **Versão do firmware**: o SDK 1.3.19 só suporta 011700, 011800 e 012000–020000.
- **Tipo de canais**: os 8 canais devem aparecer como `bipolar`.
- **sample_counter**: deve indicar 0 descontinuidades.

### 3. Colocar os elétrodos

Limpe a pele com álcool, cole os elétrodos descartáveis de ECG e depois encaixe os conectores de pressão. Vermelho = +, preto = −.
**Os elétrodos têm de ser novos, com o gel húmido**; o GND tem de estar bem ligado. Fixe os elétrodos e os cabos com fita adesiva.
Com elétrodos velhos ou com mau contacto no GND, a componente contínua deriva centenas de mV e o ruído EMG chega a centenas de µV, o que torna o sinal inutilizável. Com bom contacto, a componente contínua fica abaixo de algumas dezenas de mV e o EMG em repouso fica em 3–6 µV.

**Por omissão usa-se a montagem de 5 elétrodos** (`LAYOUT = "5pads"` em `config.py`): só os canais 1, 2 e o GND.
O EMG de cerrar os dentes e de levantar as sobrancelhas é extraído da parte de alta frequência dos canais 1 e 2.

| Etiqueta XE-293 | Função | Vermelho (+) | Preto (−) |
| --- | --- | --- | --- |
| 1 | EOG horizontal + EMG dos dentes | 1 cm por fora do canto externo do olho direito | 1 cm por fora do canto externo do olho esquerdo |
| 2 | EOG vertical + EMG das sobrancelhas | 1 cm acima da sobrancelha esquerda | Na margem superior da maçã do rosto, por baixo da órbita esquerda |
| GND | Terra | Centro da testa | |
| 3–8 | Não usados | Isolar com fita, sem tocar em metal | |

Se tiver elétrodos suficientes, pode usar a montagem de 9 elétrodos (antes de executar: `set EEGO_LAYOUT=9pads`), com os canais 3 e 4 colocados à parte:

| Etiqueta XE-293 | Função | Vermelho (+) | Preto (−) |
| --- | --- | --- | --- |
| 1 | EOG horizontal | 1 cm por fora do canto externo do olho direito | 1 cm por fora do canto externo do olho esquerdo |
| 2 | EOG vertical | 1 cm acima da sobrancelha esquerda | Logo abaixo da pálpebra inferior esquerda |
| 3 | EMG dos dentes | Músculo masséter esquerdo (onde incha ao cerrar os dentes) | No mesmo músculo, a 2 cm do vermelho |
| 4 | EMG das sobrancelhas | Testa, 2–3 cm acima da sobrancelha direita | À mesma altura, a 2 cm do vermelho |
| GND | Terra | Centro da testa | |
| 5–8 | Não usados | Cada par lado a lado atrás da orelha (apófise mastoide) | |

Se ligar os elétrodos a outros canais, basta alterar `CHANNELS` em `config.py`.

### 4. Ver o sinal

```
python live_view.py
```

Faça os movimentos seguintes e confirme que a curva correspondente reage:

- Olhar para a direita e depois para a esquerda: a linha 1 sobe e desce claramente.
- Olhar para cima e depois para baixo: a linha 2 sobe e desce claramente.
- Piscar os olhos: aparece um pico na linha 2.
- Cerrar os dentes: a linha 3 sobe claramente.
- Levantar as sobrancelhas: a linha 4 sobe claramente.

Para gravar estes dados, acrescente `--save test1.npz`.

### 5. Calibração

```
python calibrate.py
```

Siga as instruções no ecrã:

1. **Mexa só os olhos para seguir o ponto vermelho; não mexa a cabeça.**
2. Quando o aviso ficar laranja, cerre os dentes.
3. Quando o aviso ficar azul, levante as sobrancelhas.

No fim aparece a amplitude de cada movimento e se a calibração passou. Se não passou, ajuste os elétrodos conforme as indicações e calibre outra vez.

### 6. Primeiro um ensaio, depois o controlo real

Modo de ensaio: o programa só imprime as ações, sem mexer o rato.

```
python mouse_control.py --dry-run
```

Quando o ensaio estiver bem, passe ao controlo real:

```
python mouse_control.py
```

Ao arrancar, olhe para o centro do ecrã durante 2 segundos enquanto o programa mede a linha de base; depois do "bip", pode começar.

| Ação | Efeito |
| --- | --- |
| Desviar os olhos para a esquerda, direita, cima ou baixo | O cursor move-se continuamente nessa direção, mais depressa quanto maior o desvio; ao voltar a olhar para o centro, para |
| Cerrar os dentes uma vez (0,1–0,6 s) | Clique esquerdo (duas vezes rápidas = duplo clique) |
| Cerrar os dentes mais de 1 s | Pausa / retomar (som grave = pausa, som agudo = retomar) |
| Levantar as sobrancelhas uma vez | Clique direito |
| Olhar para o centro do ecrã e levantar as sobrancelhas durante 1 s | Voltar a centrar (quando a linha de base derivou e o cursor anda sozinho) |
| Teclas F8 / Esc | Pausa / sair (segurança para quem está ao lado) |

Para o modo "o cursor vai para onde se olha", acrescente `--mode absolute`. Este modo precisa de ser recentrado com frequência.

### Experiência prática (primeiro teste com o aparelho, 9/11/2026)

- Na montagem de 5 elétrodos o gesto das sobrancelhas é pouco fiável; recomenda-se `--no-brow`: o clique direito passa a ser "cerrar os dentes 0,6–1 s".
- Cerrar os dentes ou levantar as sobrancelhas estica a pele à volta dos olhos e faz saltar a componente contínua do EOG em vários mV; por isso o programa **volta a centrar automaticamente** no fim de cada gesto facial.
- A calibração pode trocar a esquerda com a direita ou medir amplitudes verticais demasiado pequenas. Pode indicá-las à mão (em µV, com sinal), por exemplo:
  `python mouse_control.py --no-brow --right-uv 300 --left-uv -300 --up-uv 150 --down-uv -120`
  Quanto menor o valor, mais sensível; se uma direção estiver invertida, troque o sinal. Também pode editar diretamente estes quatro valores em `eog` no ficheiro `calibration.json`.

### Ajustes comuns

Pode ajustar com argumentos na linha de comando ou diretamente em `config.py`.

- **Cursor demasiado rápido ou lento**: `--speed 500` (em píxeis por segundo).
- **O cursor mexe-se com o mínimo movimento dos olhos**: aumente a zona morta, por exemplo `--dead-zone 0.5`.
- **É preciso desviar muito os olhos para o cursor mexer**: diminua a zona morta, por exemplo `--dead-zone 0.25`.
- **Os cliques com os dentes falham ou disparam sozinhos**: calibre outra vez e cerre os dentes com mais força. Também pode aumentar o valor `0.35` em `calibration.py`.
- **Problemas**: guarde `calib_raw.npz` ou uma gravação feita com `live_view --save` para análise offline.

## Testes (sem hardware)

```
python test_pipeline.py          # teste automático
python live_view.py --sim        # simulação por teclado: setas = direção do olhar, J = cerrar os dentes, B = sobrancelhas
python calibrate.py --sim
python mouse_control.py --sim --dry-run
```
