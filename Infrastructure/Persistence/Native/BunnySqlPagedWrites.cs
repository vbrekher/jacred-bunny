using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace JacRed.Infrastructure.Persistence.Native;

internal sealed partial class BunnySqlClient
{
    // Chunk transport, not atomicity: every page stays on the same transaction.
    internal async Task PagedWrite(IReadOnlyList<SqlStatement> statements,CancellationToken cancellation=default)
    {
        if(statements.Count==0)throw new NativeSqlException("Empty SQL write");
        using var deadline=CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(TimeSpan.FromMinutes(2));
        using var pages=WritePages(statements).GetEnumerator();
        if(!pages.MoveNext())throw new NativeSqlException("Empty SQL write");
        var first=pages.Current;
        if(!pages.MoveNext())
        {
            await Transaction(first,write:true,deadline.Token).ConfigureAwait(false);
            return;
        }
        await using var session=await OpenWriteSession(deadline.Token).ConfigureAwait(false);
        await session.Batch(first,deadline.Token).ConfigureAwait(false);
        do { await session.Batch(pages.Current,deadline.Token).ConfigureAwait(false); } while(pages.MoveNext());
        await session.Commit(deadline.Token).ConfigureAwait(false);
    }

    static IEnumerable<SqlStatement[]> WritePages(IReadOnlyList<SqlStatement> statements)
    {
        var page=new List<SqlStatement>();
        int bytes=1024;
        foreach(var statement in statements)
        {
            // Reserve space for conditions, wrapper and a maximum-size baton.
            int size=JsonSerializer.SerializeToUtf8Bytes(Statement(statement)).Length+128;
            if(size>MaxRequest-32768)throw new NativeSqlException("Single SQL statement exceeds bounded request size");
            if(page.Count>0&&(page.Count>=512||bytes+size>1024*1024))
            {yield return page.ToArray();page.Clear();bytes=1024;}
            page.Add(statement);bytes+=size;
        }
        if(page.Count>0)yield return page.ToArray();
    }
}
