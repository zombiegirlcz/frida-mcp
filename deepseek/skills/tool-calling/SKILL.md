---
name: tool-calling
description: Presny format tool callu pro deepseek-free a qwen-free providery. Pouzij VZDY, kdyz posilas tool call (bash/read/write/edit/grep/find/ls), kdyz se ti odpoved rozpadla na thinking + text + tool call, nebo kdyz model zacal psat "Tool X does not exist" nebo si vymyslet vysledky. Obsahuje desitky spravnych prikladu a seznam nejcastejsich chyb.
---

# tool-calling - jak volat nastroje, aby to proslo

Tento skill je pro modely za `deepseek-free` a `qwen-free`. Tyto backendy
nejsou nativni OpenAI API - odpovidaji **textem** a nas bridge
(`common/toolbridge.py`) z toho textu parsuje tool cally. Kdyz text
neodpovida formatu nize, tool call se **neparsuje** a vznikne halucinace.

## Zelezna pravidla

1. **CELY VYSTUP je jen tool call(y).** Zadna veta pred, zadna po, zadne
   vysvetlovani, zadne "Ted spustim", zadne "Vysledek je".
2. **Nikdy nepis vysledek nastroje.** Vysledek doda system jako
   `[VYSLEDEK NASTROJE ...]`. Ty ho jen prectes v dalsim tahu.
3. **Nikdy nepis `[VYSLEDEK NASTROJE ...]`, `[USER]`, `[ASSISTANT]`,
   `[SYSTEM]`.** To jsou uradni znacky konverzace, ne tvuj text. Kdyz je
   opises, rozbijes session.
4. **NIKDY netvrď, ze nastroj neexistuje.** Nastroje existuji. Kdyz nevis,
   jak zavolat, posli holý JSON:
   <tool_call>{"name":"bash","arguments":{"command":"..."}}</tool_call>
5. **Mysleni (thinking) je ODDELENE od tool callu.** Do thinking bloku patri
   jen tvoje uvaha v prirozenem jazyce. Do tool callu patri **jen** blok
   <tool_call>...</tool_call>. Nikdy nemichej markup tool callu do myslenek.

## Dva formaty

### A) Kratke argumenty - holy JSON

<tool_call>
{"name": "bash", "arguments": {"command": "pwd"}}
</tool_call>

### B) Dlouhy text / vice radku - invoke + parameter

Obsah parametru se **NEescapuje** - pises ho presne jak ma byt, vcetne
novych radku, uvozovek a dolaru. Toto je preferovany format pro `write` a `edit`.

<tool_call>
<invoke name="write">
<parameter name="path">/tmp/a.txt</parameter>
<parameter name="content">radek 1
radek 2 "s uvozovkami" a $PROMENNOU
radek 3</parameter>
</invoke>
</tool_call>

Vice volani v jednom tahu = vice invoke bloku uvnitr **jednoho** tool_call.
Nebo vice samostatnych tool_call bloku.

## Priklady pro kazdy nastroj

### bash - spusteni prikazu

<tool_call>
{"name": "bash", "arguments": {"command": "ls -la /tmp"}}
</tool_call>

<tool_call>
<invoke name="bash">
<parameter name="command">cd /root/projekt && git status && git log --oneline -5</parameter>
</invoke>
</tool_call>

<tool_call>
{"name": "bash", "arguments": {"command": "grep -rn TODO src/ | head -20", "timeout": 60}}
</tool_call>

### read - precteni souboru

<tool_call>
{"name": "read", "arguments": {"path": "/etc/hostname"}}
</tool_call>

<tool_call>
{"name": "read", "arguments": {"path": "/root/app.py", "offset": 100, "limit": 50}}
</tool_call>

### write - zapis celeho souboru (VZDY format B)

<tool_call>
<invoke name="write">
<parameter name="path">/tmp/hello.py</parameter>
<parameter name="content">#!/usr/bin/env python3
def main():
    print("ahoj")
    for i in range(3):
        print(i)

if __name__ == "__main__":
    main()</parameter>
</invoke>
</tool_call>

### edit - cilena zmena (VZDY format B)

oldText musi presne odpovidat tomu, co v souboru je. Kdyz menis vic mist,
posli vic edit invoke bloku v jednom tool_call.

<tool_call>
<invoke name="edit">
<parameter name="path">/tmp/hello.py</parameter>
<parameter name="oldText">    print("ahoj")</parameter>
<parameter name="newText">    print("nazdar")</parameter>
</invoke>
</tool_call>

<tool_call>
<invoke name="edit">
<parameter name="path">/tmp/app.py</parameter>
<parameter name="oldText">DEBUG = False</parameter>
<parameter name="newText">DEBUG = True</parameter>
</invoke>
<invoke name="edit">
<parameter name="path">/tmp/app.py</parameter>
<parameter name="oldText">PORT = 8000</parameter>
<parameter name="newText">PORT = 9000</parameter>
</invoke>
</tool_call>

### grep - hledani v souborech

<tool_call>
{"name": "grep", "arguments": {"pattern": "TODO", "path": "/src", "include": "*.py"}}
</tool_call>

### find / ls - hledani souboru

<tool_call>
{"name": "find", "arguments": {"pattern": "*.log", "path": "/var/log"}}
</tool_call>

<tool_call>
{"name": "ls", "arguments": {"path": "/root/projekt"}}
</tool_call>

## Paralelni volani

Kdyz potrebujes vic nezavislych veci najednou, posli **vsechna volani
v jednom tahu** - ne jedno za druhym.

<tool_call>
{"name": "bash", "arguments": {"command": "pwd"}}
</tool_call>
<tool_call>
{"name": "bash", "arguments": {"command": "whoami"}}
</tool_call>
<tool_call>
{"name": "read", "arguments": {"path": "/etc/os-release"}}
</tool_call>

Nebo jeden tool_call s vice invoke:

<tool_call>
<invoke name="read">
<parameter name="path">/etc/hostname</parameter>
</invoke>
<invoke name="read">
<parameter name="path">/etc/os-release</parameter>
</invoke>
</tool_call>

## Co NIKDY nedelat

**1. Text pred tool callem** - bridge to nezparsuje nebo to zustane v historii:

<tool_call>
{"name": "bash", "arguments": {"command": "ls"}}
</tool_call>
Pred tim jsem napsal vetu - to je spatne.

**2. Vymysleny vysledek** - NIKDY nepis, co nastroj vratil. Pockej na system.

**3. Markdown fence kolem JSONu** - bridge toleruje trojite zpetne uvozovky,
ale nedelej to. Posli holy JSON v tool_call.

**4. Zkomolene tagy** - <|DSML|tool_calls>, <call_call>, < calls> nejsou
spravne. Pouzivej **presne** <tool_call> a <invoke>.

**5. Argumenty zabalene jeste jednou** - tohle pi odmitne:

<tool_call>
{"name": "bash", "arguments": {"arguments": "{\\"command\\": \\"ls\\"}"}}
</tool_call>

Spravne (rozbalene):

<tool_call>
{"name": "bash", "arguments": {"command": "ls"}}
</tool_call>

**6. timeout jako string** - musi to byt cislo:

<tool_call>
{"name": "bash", "arguments": {"command": "sleep 5", "timeout": "60"}}
</tool_call>

Spravne:

<tool_call>
{"name": "bash", "arguments": {"command": "sleep 5", "timeout": 60}}
</tool_call>

**7. Smichani myslenek a tool callu** - thinking blok obsahuje markup:

Spatne (markup v thinkingu):
```
Premyslim: <invoke name="bash"><parameter name="command">ls</parameter></invoke>
```

Spravne: myslenky v prirozenem jazyce, tool call **uplne oddelene**.

## Jak vypada spravny tah

**Tah 1 - potrebuji data:** poslu jen tool call.

<tool_call>
{"name": "bash", "arguments": {"command": "cat /etc/hostname"}}
</tool_call>

**Tah 2 - system posle vysledek** jako [VYSLEDEK NASTROJE bash] s obsahem.

**Tah 3 - mam vse, pisu finalni odpoved** (zadny tool call).

## Diagnostika: proc se mi to rozpadlo

Kdyz se ti odpoved rozpadla (thinking + text + tool call), zkontroluj:

- Poslal jsi **jen** tool call, nebo i prose okolo?
- Je JSON validni? (uvozovky, carky, zadny trailing comma)
- Je arguments **rozbaleny** objekt (ne string, ne znovu zabaleny)?
- Nepouzil jsi zkomoleny tag misto <tool_call>?
- Neopsal jsi do odpovedi [VYSLEDEK NASTROJE ...] nebo [USER]?

Kdyz si nejsi jisty, posli **nejjednodussi moznou** formu:

<tool_call>
{"name": "bash", "arguments": {"command": "echo test"}}
</tool_call>

a pokracuj od ni.

## Mapovani nastroju (bezny pi harness)

- bash   -> spusteni shell prikazu         (parametr: command, timeout)
- read   -> precteni souboru               (parametr: path, offset, limit)
- write  -> zapis celeho souboru           (parametr: path, content)  [format B]
- edit   -> cilena zmena                   (parametr: path, oldText, newText) [format B]
- grep   -> hledani v souborech            (parametr: pattern, path, include)
- find   -> hledani souboru                (parametr: pattern, path)
- ls     -> vypis adresare                 (parametr: path)

Kdyz si nejsi jisty nazvem nastroje, posli holy JSON s parametrem, ktery
jednoznacne patrne jen jednomu nastroji - bridge ho dovodi sam.
