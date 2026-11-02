#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""クラス図の要素の別名 (コーディング時の名前) を .qeax とテキストの間で往復させる。

    python scripts/ea_aliases.py export              # EA -> docs/design/別名-クラス指定.md
    python scripts/ea_aliases.py import --dry-run    # 差分だけ表示
    python scripts/ea_aliases.py import              # テキスト -> EA (自動バックアップあり)

対象は <モデル>/クラス パッケージ直下のクラスと enum。別名の格納先:

- クラス / enum              t_object.Alias
- 属性 / enum リテラル        t_attribute.Style
- 操作                       t_operation.Style
- 関連端のロール              t_connector.SourceStyle / DestStyle の `alias=…;`

ロールはそれを (メンバとして) 持つ側、つまり関連の反対端のクラスの節に `ロール名 : ロール側クラス`
として並べる。export は既存ファイルの「備考」列を名前をキーに引き継ぐ。

属性の型 (t_attribute.Type / Classifier)、操作の返り値の型 (t_operation.Type / Classifier) と
引数 (t_operationparams) もついでに往復させる。同じパッケージのクラス / enum を型にするときは
見出しと同じ `enum 名前` / `クラス 名前` と書き、Classifier をその Object_ID にする。それ以外
(int, void など) は Type にそのまま入れ、Classifier は '0' (EA の組み込み型の扱い)。
引数は `名前: 型, 名前: 型` と書く (すべて in)。書き換えるときは操作の引数を丸ごと作り直すが、
同名の引数の GUID と Notes は引き継ぐ。
"""
import argparse
import datetime
import glob
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import unicodedata
import uuid

MODELS = {
    '設計-クラス指定': os.path.join('docs', 'design', '別名-クラス指定.md'),
}
CLASS_PKG = 'クラス'
KIND_ORDER = ('要素', '属性', 'リテラル', '操作', 'ロール')


class Row:
    def __init__(self, alias, setter, type=None, type_setter=None, params=None, params_setter=None):
        self.alias, self.setter, self.type, self.type_setter = alias, setter, type, type_setter
        self.params, self.params_setter = params, params_setter


HEADER = """<!-- このファイルと EA (話題沸騰ポット.qeax) は scripts/ea_aliases.py で往復させる。
     export: EA -> このファイル / import: このファイル -> EA

     書式の決まり:
     - `## クラス 名前` / `## enum 名前` の名前は EA の Name と完全一致させる
       (クラスと enum に同名のもの (保温モード) があるので種類も見出しに含める)
     - 表の「種別」「名前」がキー。ロールの名前は `ロール名 : ロール側クラス`
     - 「別名」「型」が空のセルは未記入とみなし、import では何もしない (EA 側の削除は手作業で)
     - 「型」は属性の型と操作の返り値の型。パッケージ内の型は `enum 名前` / `クラス 名前`、
       それ以外は int, void などそのまま
     - 「引数」は操作だけ。`名前: 型, 名前: 型` (型の書き方は「型」と同じ)。引数なしは空のまま
     - 「備考」は EA には書き込まない (メモ用)
-->

# 別名 (%s/%s)
"""


def find_db():
    hits = [h for h in glob.glob('*.qeax')
            if unicodedata.normalize('NFC', h) == '話題沸騰ポット.qeax']
    if not hits:
        sys.exit('話題沸騰ポット.qeax が見つからない。リポジトリのルートで実行すること。')
    return hits[0]


def ea_running():
    try:
        out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq EA.exe'],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False
    return 'EA.exe' in out


def style_get(style, key):
    for part in (style or '').split(';'):
        k, _, v = part.partition('=')
        if k == key:
            return v
    return None


def style_set(style, key, value):
    parts = [p for p in (style or '').split(';') if p and p.partition('=')[0] != key]
    if value:
        parts.append('%s=%s' % (key, value))
    return ''.join(p + ';' for p in parts)


def class_pkg(con, model):
    row = con.execute('select c.Package_ID from t_package c join t_package m on c.Parent_ID=m.Package_ID '
                      'where m.Name=? and c.Name=?', (model, CLASS_PKG)).fetchone()
    if not row:
        sys.exit('パッケージ %s/%s が見つからない。' % (model, CLASS_PKG))
    return row[0]


def collect(con, pkg):
    """{見出し: {'type':..., 'id':..., 'rows': {(種別, 名前): Row}}} を返す。

    見出しは `クラス 名前` / `enum 名前`。Row の setter(con, alias) がその行の別名を、
    type_setter(con, 型) が属性の型 / 操作の返り値の型を、params_setter(con, 引数) が操作の引数を
    書き込む (該当しない行は None)。"""
    objs = con.execute("select Object_ID,Name,Object_Type,Alias from t_object where Package_ID=? "
                       "and Object_Type in ('Class','Enumeration') order by Object_Type,Name",
                       (pkg,)).fetchall()
    names = {o['Object_ID']: o['Name'] for o in objs}
    heads = {o['Object_ID']: '%s %s' % ('enum' if o['Object_Type'] == 'Enumeration' else 'クラス', o['Name'])
             for o in objs}
    if len(set(heads.values())) != len(heads):
        dup = sorted({n for n in heads.values() if list(heads.values()).count(n) > 1})
        sys.exit('同名の要素がある (見出しをキーにできない): %s' % ', '.join(dup))
    out = {}
    for o in objs:
        rows = {}
        oid = o['Object_ID']
        rows[('要素', o['Name'])] = Row(o['Alias'], lambda c, v, oid=oid: c.execute(
            'update t_object set Alias=? where Object_ID=?', (v, oid)))
        enum = o['Object_Type'] == 'Enumeration'
        for a in con.execute('select ID,Name,Style,Type,Classifier from t_attribute where Object_ID=? '
                             'order by Pos,ID', (oid,)):
            set_alias = lambda c, v, i=a['ID']: c.execute('update t_attribute set Style=? where ID=?', (v, i))
            if enum:
                rows[('リテラル', a['Name'])] = Row(a['Style'], set_alias)
                continue
            rows[('属性', a['Name'])] = Row(a['Style'], set_alias, shown_type(a, heads),
                                          lambda c, v, i=a['ID']: c.execute(
                                              'update t_attribute set Type=?, Classifier=? where ID=?',
                                              (*resolve_type(v, heads), i)))
        for p in con.execute('select OperationID,Name,Style,Type,Classifier from t_operation where Object_ID=? '
                             'order by Pos,OperationID', (oid,)):
            i = p['OperationID']
            params = ', '.join('%s: %s' % (q['Name'], shown_type(q, heads)) for q in con.execute(
                'select Name,Type,Classifier from t_operationparams where OperationID=? order by Pos', (i,)))
            rows[('操作', p['Name'])] = Row(
                p['Style'], lambda c, v, i=i: c.execute('update t_operation set Style=? where OperationID=?', (v, i)),
                shown_type(p, heads),
                lambda c, v, i=i: c.execute('update t_operation set Type=?, Classifier=? where OperationID=?',
                                            (*resolve_type(v, heads), i)),
                params, lambda c, v, i=i: set_params(c, i, v, heads))
        out[heads[oid]] = {'type': o['Object_Type'], 'id': oid, 'rows': rows}
    # ロール: Source 端のロールは End 側クラスのメンバ、Dest 端のロールは Start 側クラスのメンバ
    for k in con.execute("select Connector_ID,Start_Object_ID,End_Object_ID,SourceRole,DestRole,"
                         "SourceStyle,DestStyle from t_connector where Connector_Type in "
                         "('Association','Aggregation') order by Connector_ID"):
        for role, side, owner, col in ((k['SourceRole'], k['Start_Object_ID'], k['End_Object_ID'], 'SourceStyle'),
                                       (k['DestRole'], k['End_Object_ID'], k['Start_Object_ID'], 'DestStyle')):
            if not role or side not in names or owner not in names:
                continue
            key = ('ロール', '%s : %s' % (role, names[side]))
            rows = out[heads[owner]]['rows']
            if key in rows:
                sys.exit('%s に同じロールが複数ある: %s' % (heads[owner], key[1]))
            rows[key] = Row(style_get(k[col], 'alias'), lambda c, v, i=k['Connector_ID'], col=col: c.execute(
                'update t_connector set %s=? where Connector_ID=?' % col,
                (style_set(c.execute('select %s from t_connector where Connector_ID=?' % col, (i,)).fetchone()[0],
                           'alias', v), i)))
    return out


def shown_type(row, heads):
    """Type/Classifier の行を表の「型」の書き方にする。"""
    cls = row['Classifier']
    if cls and cls.isdigit() and int(cls) in heads:
        return heads[int(cls)]
    return row['Type']


def resolve_type(value, heads):
    """表の「型」の書き方 -> (Type, Classifier)。"""
    target = {h: oid for oid, h in heads.items()}.get(value)
    if target:
        return value.split(' ', 1)[1], str(target)
    return value, '0'


def set_params(con, op_id, value, heads):
    old = {r['Name']: r for r in con.execute('select Name,Notes,ea_guid from t_operationparams '
                                             'where OperationID=?', (op_id,))}
    con.execute('delete from t_operationparams where OperationID=?', (op_id,))
    for pos, part in enumerate(x for x in value.split(',') if x.strip()):
        name, _, typ = (t.strip() for t in part.partition(':'))
        if not name or not typ:
            sys.exit('引数の書き方が違う (名前: 型): %s' % part)
        prev = old.get(name)
        con.execute("insert into t_operationparams (OperationID,Name,Type,Classifier,Pos,Const,Kind,Notes,ea_guid) "
                    "values (?,?,?,?,?,0,'in',?,?)",
                    (op_id, name, *resolve_type(typ, heads), pos, prev['Notes'] if prev else None,
                     prev['ea_guid'] if prev else ea_guid()))


def ea_guid():
    u = str(uuid.uuid4()).upper().split('-')
    u[2] = u[2].lower()
    return '{' + '-'.join(u) + '}'


def parse_md(path):
    """{見出し: {(種別, 名前): {列名: 値}}}"""
    doc, cur, cols = {}, None, None
    if not os.path.exists(path):
        return doc
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            m = re.match(r'^## (.+?)\s*$', line)
            if m:
                cur = doc.setdefault(m.group(1), {})
                continue
            if cur is None or not line.startswith('|'):
                continue
            cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
            if cells[0] == '種別':
                cols = cells
                continue
            if len(cells) < 3 or not cells[0] or set(cells[0]) <= set('-:'):
                continue
            cur[(cells[0], cells[1])] = {k: v.replace('\\|', '|') for k, v in zip(cols, cells)}
    return doc


def esc(s):
    return (s or '').replace('|', '\\|')


def do_export(con, pkg, model, path):
    old = parse_md(path)
    data = collect(con, pkg)
    lines = [HEADER % (model, CLASS_PKG)]
    for name in sorted(data, key=lambda n: (data[n]['type'] != 'Class', n)):
        d = data[name]
        lines.append('## %s\n' % name)
        lines.append('| 種別 | 名前 | 別名 | 型 | 引数 | 備考 |')
        lines.append('|---|---|---|---|---|---|')
        for key in sorted(d['rows'], key=lambda k: KIND_ORDER.index(k[0])):
            row = d['rows'][key]
            note = old.get(name, {}).get(key, {}).get('備考', '')
            lines.append('| %s | %s | %s | %s | %s | %s |' % (key[0], esc(key[1]), esc(row.alias), esc(row.type),
                                                          esc(row.params), note))
        lines.append('')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines))
    print('wrote', path)


def do_import(con, pkg, path, dry):
    doc = parse_md(path)
    data = collect(con, pkg)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    changed = 0
    for name, rows in doc.items():
        if name not in data:
            print('!! EA に無い要素: %s' % name)
            continue
        touched = False
        for key, cells in rows.items():
            if key not in data[name]['rows']:
                print('!! %s に無い%s: %s' % (name, key[0], key[1]))
                continue
            row = data[name]['rows'][key]
            for what, new, cur, setter in (('別名', cells.get('別名'), row.alias, row.setter),
                                           ('型', cells.get('型'), row.type, row.type_setter),
                                           ('引数', cells.get('引数'), row.params, row.params_setter)):
                if not new or new == (cur or ''):
                    continue
                if not setter:
                    print('!! %s %s %s: %sは書き込めない' % (name, key[0], key[1], what))
                    continue
                print('%s %s %s %s: %s -> %s' % (name, key[0], key[1], what, cur or '(なし)', new))
                if not dry:
                    setter(con, new)
                changed += 1
                touched = True
        if touched and not dry:
            con.execute('update t_object set ModifiedDate=? where Object_ID=?', (now, data[name]['id']))
    for name in sorted(set(data) - set(doc)):
        print('-- テキストに無い要素 (未変更): %s' % name)
    print('%d 件%s' % (changed, ' (dry-run)' if dry else ''))
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-m', '--model', default='設計-クラス指定', choices=sorted(MODELS))
    ap.add_argument('cmd', choices=('export', 'import'))
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    path = MODELS[args.model]
    db = find_db()
    writing = args.cmd == 'import' and not args.dry_run
    if writing and ea_running():
        sys.exit('EA が起動中。閉じてから実行すること (起動中の変更は EA 終了時に上書きされる)。')
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    pkg = class_pkg(con, args.model)
    if args.cmd == 'export':
        do_export(con, pkg, args.model, path)
        return
    if writing:
        bak = '%s.bak_%s' % (db, datetime.datetime.now().strftime('%Y%m%d_%H%M%S'))
        shutil.copy2(db, bak)
        print('backup:', bak)
    if do_import(con, pkg, path, args.dry_run) and writing:
        con.commit()
        print('integrity:', con.execute('pragma integrity_check').fetchone()[0])


if __name__ == '__main__':
    main()
