"""EA (.qeax / SQLite) の要素一式を、ID と GUID を振り直してコピーする部品。

EA の GUI のパッケージのコピーは、別ファイルからのコピーや「コピーしたインスタンスの分類子を
コピー先のクラスへ付け替える」ことができないので、その代わりに使う。

使い方 (scripts/ 以外から使う一回きりの手順スクリプトで):

    cp = Copier(src_con, dst_con)
    new_pkg = cp.copy_packages(src_pkg_id, dst_parent_pkg_id)    # パッケージの木だけ作る
    cp.copy_objects(obj_ids, resolve_parent=..., resolve_classifier=...)
    cp.copy_diagrams(diagram_ids)
    cp.copy_connectors(resolve_op_guid=...)
    cp.copy_xrefs()

src と dst は同じ接続でもよい。コピー元は読むだけ。ID の参照先:

- t_object: Package_ID, ParentID, Classifier(+Classifier_guid), Note の PDATA2 (制約/シナリオの持ち主)、
  Package 要素の PDATA1
- t_diagram: Package_ID, ParentID (状態マシン図の持ち主)
- t_diagramobjects / t_diagramlinks: Diagram_ID, Object_ID / ConnectorID (DUID はそのまま)
- t_connector: Start/End_Object_ID, DiagramID (メッセージ), SourceIsAggregate (メッセージが乗るリンク)
- t_connectortag: operation_guid (メッセージの操作)
- t_xref: Client / Supplier / Description 中の GUID (遷移→トリガー、既定の図、図の設定)
- t_document: 図の補助データ (ElementID = 図の GUID)
"""
import re
import uuid


def new_guid():
    return '{%s}' % str(uuid.uuid4()).upper()


def rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert(con, table, row, pk=None):
    r = {k: v for k, v in row.items() if k != pk}
    cur = con.execute(f"insert into {table} ({','.join('[%s]' % k for k in r)}) "
                      f"values ({','.join('?' * len(r))})", list(r.values()))
    return cur.lastrowid


class Copier:
    def __init__(self, src, dst):
        self.src, self.dst = src, dst
        self.pkg, self.obj, self.diag, self.conn, self.op = {}, {}, {}, {}, {}
        self.guid = {}          # 旧 GUID -> 新 GUID
        self.log = []

    # ------------------------------------------------------------ packages
    def copy_packages(self, src_pkg, dst_parent, name=None):
        p = rows(self.src, "select * from t_package where Package_ID=?", (src_pkg,))[0]
        g = new_guid(); self.guid[p['ea_guid']] = g
        p.update(Parent_ID=dst_parent, ea_guid=g, Name=name or p['Name'])
        nid = insert(self.dst, 't_package', p, 'Package_ID')
        self.pkg[src_pkg] = nid
        po = rows(self.src, "select * from t_object where Object_Type='Package' and PDATA1=?", (str(src_pkg),))
        if po:
            o = po[0]
            o.update(Package_ID=dst_parent, PDATA1=str(nid), ea_guid=g, Name=p['Name'])
            self.obj[o['Object_ID']] = insert(self.dst, 't_object', o, 'Object_ID')
        for (c,) in self.src.execute("select Package_ID from t_package where Parent_ID=?", (src_pkg,)).fetchall():
            self.copy_packages(c, nid)
        return nid

    # ------------------------------------------------------------ objects
    def copy_objects(self, ids, dst_pkg=None, resolve_parent=None, resolve_classifier=None):
        """ids を全部コピーしてから参照を張り直す。ParentID/Classifier がコピー集合の外を指すときは
        resolve_parent / resolve_classifier (旧 id -> 新 id、None なら 0) で決める。"""
        todo = [rows(self.src, "select * from t_object where Object_ID=?", (i,))[0] for i in ids]
        for o in todo:
            g = new_guid(); self.guid[o['ea_guid']] = g
            o2 = dict(o, ea_guid=g)
            o2['Package_ID'] = dst_pkg if dst_pkg is not None else self.pkg[o['Package_ID']]
            self.obj[o['Object_ID']] = insert(self.dst, 't_object', o2, 'Object_ID')
        for o in todo:
            nid = self.obj[o['Object_ID']]
            upd = {}
            if o['ParentID']:
                upd['ParentID'] = self.obj.get(o['ParentID']) or (resolve_parent(o['ParentID']) if resolve_parent else 0) or 0
                if not upd['ParentID']: self.log.append(f"parent lost: {o['Name']} ({o['Object_Type']})")
            if o['Classifier']:
                cl = self.obj.get(o['Classifier']) or (resolve_classifier(o['Classifier']) if resolve_classifier else 0) or 0
                upd['Classifier'] = cl
                upd['Classifier_guid'] = self.dst.execute("select ea_guid from t_object where Object_ID=?", (cl,)).fetchone()[0] if cl else None
                if not cl: self.log.append(f"classifier lost: {o['Name']}")
            if o['Object_Type'] == 'Note' and o['PDATA1'] in ('Constraint', 'Scenario') and o['PDATA2']:
                upd['PDATA2'] = str(self.obj.get(int(o['PDATA2']), 0))
            if upd:
                self.dst.execute(f"update t_object set {','.join(k+'=?' for k in upd)} where Object_ID=?",
                                 list(upd.values()) + [nid])
            self._copy_features(o['Object_ID'], nid)

    def _copy_features(self, old, new):
        for a in rows(self.src, "select * from t_attribute where Object_ID=?", (old,)):
            g = new_guid(); self.guid[a['ea_guid']] = g
            insert(self.dst, 't_attribute', dict(a, Object_ID=new, ea_guid=g), 'ID')
        for op in rows(self.src, "select * from t_operation where Object_ID=?", (old,)):
            g = new_guid(); self.guid[op['ea_guid']] = g
            nop = insert(self.dst, 't_operation', dict(op, Object_ID=new, ea_guid=g), 'OperationID')
            self.op[op['OperationID']] = nop
            for p in rows(self.src, "select * from t_operationparams where OperationID=?", (op['OperationID'],)):
                g = new_guid(); self.guid[p['ea_guid']] = g
                insert(self.dst, 't_operationparams', dict(p, OperationID=nop, ea_guid=g))
            for t in rows(self.src, "select * from t_operationtag where ElementID=?", (op['OperationID'],)):
                insert(self.dst, 't_operationtag', dict(t, ElementID=nop, ea_guid=new_guid()), 'PropertyID')
        for c in rows(self.src, "select * from t_objectconstraint where Object_ID=?", (old,)):
            insert(self.dst, 't_objectconstraint', dict(c, Object_ID=new))
        for s in rows(self.src, "select * from t_objectscenarios where Object_ID=?", (old,)):
            g = new_guid(); self.guid[s['ea_guid']] = g
            insert(self.dst, 't_objectscenarios', dict(s, Object_ID=new, ea_guid=g))
        for p in rows(self.src, "select * from t_objectproperties where Object_ID=?", (old,)):
            insert(self.dst, 't_objectproperties', dict(p, Object_ID=new, ea_guid=new_guid()), 'PropertyID')

    # ------------------------------------------------------------ diagrams
    def copy_diagrams(self, ids, dst_pkg=None):
        for d in [rows(self.src, "select * from t_diagram where Diagram_ID=?", (i,))[0] for i in ids]:
            g = new_guid(); self.guid[d['ea_guid']] = g
            d2 = dict(d, ea_guid=g,
                      Package_ID=dst_pkg if dst_pkg is not None else self.pkg[d['Package_ID']],
                      ParentID=self.obj.get(d['ParentID'], 0) if d['ParentID'] else 0)
            if d['ParentID'] and not d2['ParentID']: self.log.append(f"diagram parent lost: {d['Name']}")
            nid = insert(self.dst, 't_diagram', d2, 'Diagram_ID')
            self.diag[d['Diagram_ID']] = nid
            for do in rows(self.src, "select * from t_diagramobjects where Diagram_ID=?", (d['Diagram_ID'],)):
                if do['Object_ID'] not in self.obj:
                    self.log.append(f"diagram {d['Name']}: object {do['Object_ID']} not copied, dropped"); continue
                insert(self.dst, 't_diagramobjects', dict(do, Diagram_ID=nid, Object_ID=self.obj[do['Object_ID']]), 'Instance_ID')

    # ------------------------------------------------------------ connectors
    def copy_connectors(self, resolve_op_guid=None):
        """両端ともコピー済みの接続をすべてコピーする。メッセージは図もコピー済みのものだけ。"""
        ids = list(self.obj)
        q = ','.join(map(str, ids))
        cands = rows(self.src, f"select * from t_connector where Start_Object_ID in ({q}) and End_Object_ID in ({q})")
        done = []
        for c in cands:
            if c['Connector_ID'] in self.conn: continue
            if c['DiagramID'] and c['DiagramID'] not in self.diag: continue
            g = new_guid(); self.guid[c['ea_guid']] = g
            c2 = dict(c, ea_guid=g, Start_Object_ID=self.obj[c['Start_Object_ID']], End_Object_ID=self.obj[c['End_Object_ID']],
                      DiagramID=self.diag.get(c['DiagramID'], 0) if c['DiagramID'] else c['DiagramID'])
            self.conn[c['Connector_ID']] = insert(self.dst, 't_connector', c2, 'Connector_ID')
            done.append(c)
        for c in done:   # メッセージが乗るリンク
            if c['Connector_Type'] == 'Collaboration' and c['SourceIsAggregate']:
                self.dst.execute("update t_connector set SourceIsAggregate=? where Connector_ID=?",
                                 (self.conn.get(c['SourceIsAggregate'], 0), self.conn[c['Connector_ID']]))
                if c['SourceIsAggregate'] not in self.conn: self.log.append(f"message {c['Name']}: link not copied")
        dropped = 0
        for c in done:
            for t in rows(self.src, "select * from t_connectortag where ElementID=?", (c['Connector_ID'],)):
                t2 = dict(t, ElementID=self.conn[c['Connector_ID']], ea_guid=new_guid())
                if t['Property'] == 'operation_guid':
                    v = resolve_op_guid(t['VALUE'], self.conn[c['Connector_ID']]) if resolve_op_guid else self.guid.get(t['VALUE'], t['VALUE'])
                    if not v: dropped += 1; continue
                    t2['VALUE'] = v
                insert(self.dst, 't_connectortag', t2, 'PropertyID')
        if dropped: self.log.append(f"operation_guid dropped (no matching op): {dropped}")
        for old_d, new_d in self.diag.items():
            for dl in rows(self.src, "select * from t_diagramlinks where DiagramID=?", (old_d,)):
                if dl['ConnectorID'] in self.conn:
                    insert(self.dst, 't_diagramlinks', dict(dl, DiagramID=new_d, ConnectorID=self.conn[dl['ConnectorID']]), 'Instance_ID')

    # ------------------------------------------------------------ xref / documents
    def copy_xrefs(self):
        olds = list(self.guid)
        for i in range(0, len(olds), 400):
            chunk = olds[i:i + 400]
            for x in rows(self.src, f"select * from t_xref where Client in ({','.join('?' * len(chunk))})", chunk):
                x2 = dict(x, XrefID=new_guid(), Client=self.guid[x['Client']])
                if x['Supplier'] in self.guid: x2['Supplier'] = self.guid[x['Supplier']]
                if x['Description']:
                    x2['Description'] = re.sub(r'\{[0-9A-Fa-f-]{36}\}', lambda m: self.guid.get(m.group(0), m.group(0)), x['Description'])
                insert(self.dst, 't_xref', x2)
            for d in rows(self.src, f"select * from t_document where ElementID in ({','.join('?' * len(chunk))})", chunk):
                insert(self.dst, 't_document', dict(d, DocID=new_guid(), ElementID=self.guid[d['ElementID']]))


def descendants(con, roots):
    """ParentID でたどった子孫 (roots 自身を含む)。"""
    out, todo = [], list(roots)
    while todo:
        x = todo.pop()
        out.append(x)
        todo += [r[0] for r in con.execute("select Object_ID from t_object where ParentID=?", (x,)).fetchall()]
    return out


def package_tree(con, pkg):
    out, todo = [], [pkg]
    while todo:
        p = todo.pop(); out.append(p)
        todo += [r[0] for r in con.execute("select Package_ID from t_package where Parent_ID=?", (p,)).fetchall()]
    return out


def check_refs(con):
    """コピー後の参照切れを数える (0 なら健全)。"""
    q = {
        'object package': "select count(*) from t_object o where not exists (select 1 from t_package p where p.Package_ID=o.Package_ID)",
        'object parent': "select count(*) from t_object o where ifnull(ParentID,0)!=0 and not exists (select 1 from t_object x where x.Object_ID=o.ParentID)",
        'object classifier': "select count(*) from t_object o where ifnull(Classifier,0)!=0 and not exists (select 1 from t_object x where x.Object_ID=o.Classifier)",
        'classifier guid': "select count(*) from t_object o where ifnull(Classifier,0)!=0 and Classifier_guid != (select ea_guid from t_object x where x.Object_ID=o.Classifier)",
        'diagram package': "select count(*) from t_diagram d where not exists (select 1 from t_package p where p.Package_ID=d.Package_ID)",
        'diagram parent': "select count(*) from t_diagram d where ifnull(ParentID,0)!=0 and not exists (select 1 from t_object x where x.Object_ID=d.ParentID)",
        'diagramobject': "select count(*) from t_diagramobjects d where not exists (select 1 from t_object x where x.Object_ID=d.Object_ID) or not exists (select 1 from t_diagram g where g.Diagram_ID=d.Diagram_ID)",
        'diagramlink': "select count(*) from t_diagramlinks d where not exists (select 1 from t_connector c where c.Connector_ID=d.ConnectorID) or not exists (select 1 from t_diagram g where g.Diagram_ID=d.DiagramID)",
        'connector ends': "select count(*) from t_connector c where not exists (select 1 from t_object x where x.Object_ID=c.Start_Object_ID) or not exists (select 1 from t_object x where x.Object_ID=c.End_Object_ID)",
        'message link': "select count(*) from t_connector c where Connector_Type='Collaboration' and not exists (select 1 from t_connector l where l.Connector_ID=c.SourceIsAggregate)",
        'message diagram': "select count(*) from t_connector c where Connector_Type='Collaboration' and not exists (select 1 from t_diagram g where g.Diagram_ID=c.DiagramID)",
        'note owner': "select count(*) from t_object o where Object_Type='Note' and PDATA1 in ('Constraint','Scenario') and not exists (select 1 from t_object x where x.Object_ID=cast(o.PDATA2 as integer))",
        'op tag guid': "select count(*) from t_connectortag t where Property='operation_guid' and not exists (select 1 from t_operation o where o.ea_guid=t.VALUE)",
        'dup guid': "select count(*) - count(distinct g) from (select ea_guid g from t_object union all select ea_guid from t_connector union all select ea_guid from t_diagram union all select ea_guid from t_operation union all select ea_guid from t_attribute)",
    }
    return {k: con.execute(s).fetchone()[0] for k, s in q.items()}
