"""ASGI transport for the shared application service, with bounded request bodies.

Uvicorn owns HTTP sockets. The legacy handler dispatches domain routes through
an in-memory transport; it never opens a second HTTP server.
"""
import io
import os
from types import SimpleNamespace
from email.parser import BytesParser
from fastapi import FastAPI, Request
from starlette.responses import Response, JSONResponse
from starlette.concurrency import run_in_threadpool
from .app import Handler, AUTH_REQUIRED

app=FastAPI(title='Alphacentauric Business Intelligence',docs_url=None,redoc_url=None,openapi_url=None)

def dispatch(method,path,headers,body,client):
    h=Handler.__new__(Handler)
    h.command=method;h.path=path;h.headers=headers;h.request_version='HTTP/1.1'
    h.requestline=method+' '+path+' HTTP/1.1';h.client_address=(client,0)
    h.server=SimpleNamespace(server_port=int(os.environ.get('BI_PORT','8000')))
    h.rfile=io.BytesIO(body);h.wfile=io.BytesIO()
    (h.do_GET if method=='GET' else h.do_POST)()
    head,content=h.wfile.getvalue().split(b'\r\n\r\n',1)
    first,rest=head.split(b'\r\n',1)
    parsed=BytesParser().parsebytes(rest+b'\r\n\r\n')
    response=Response(content,status_code=int(first.split()[1]))
    response.raw_headers=[(k.lower().encode(),v.encode()) for k,v in parsed.items() if k.lower() not in ('server','date')]
    return response

@app.api_route('/{path:path}',methods=['GET','POST'])
async def route(request:Request,path:str):
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>=15*1024*1024:return JSONResponse({'error':'Request too large'},413)
    headers=dict(request.headers);headers['Content-Length']=str(len(data))
    # email.message provides the same case-insensitive lookup as HTTP headers.
    from email.message import Message
    message=Message()
    for k,v in headers.items():message[k]=v
    return await run_in_threadpool(dispatch,request.method,request.url.path,message,bytes(data),request.client.host if request.client else 'unknown')

def main():
    if not AUTH_REQUIRED:raise SystemExit('ASGI mode requires BI_AUTH_REQUIRED=1; create an account first.')
    import uvicorn
    uvicorn.run(app,host=os.environ.get('BI_BIND_HOST','127.0.0.1'),port=int(os.environ.get('BI_PORT','8000')),workers=1,proxy_headers=False,limit_concurrency=32,timeout_keep_alive=5)

if __name__=='__main__':main()
