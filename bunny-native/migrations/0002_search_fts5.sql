-- Requires FTS5 on the target SQLite/libSQL engine. Enforce with preflight.
-- FTS5 ranking is not yet proven equivalent to original FastDbIndex.
CREATE VIRTUAL TABLE IF NOT EXISTS torrent_fts USING fts5(
 title,name,original_name,content='torrents',content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS torrent_ai AFTER INSERT ON torrents BEGIN
 INSERT INTO torrent_fts(rowid,title,name,original_name)
 VALUES(new.id,new.title,new.name,new.original_name);
END;
CREATE TRIGGER IF NOT EXISTS torrent_ad AFTER DELETE ON torrents BEGIN
 INSERT INTO torrent_fts(torrent_fts,rowid,title,name,original_name)
 VALUES('delete',old.id,old.title,old.name,old.original_name);
END;
CREATE TRIGGER IF NOT EXISTS torrent_au AFTER UPDATE ON torrents BEGIN
 INSERT INTO torrent_fts(torrent_fts,rowid,title,name,original_name)
 VALUES('delete',old.id,old.title,old.name,old.original_name);
 INSERT INTO torrent_fts(rowid,title,name,original_name)
 VALUES(new.id,new.title,new.name,new.original_name);
END;
