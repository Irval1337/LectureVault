"""Production-overlay acceptance with local TLS and authenticated STARTTLS SMTP.

Disposable volumes only. No real DNS, external mail, AI calls or host CA changes.
Run with .venv/bin/python tests/run_https.py.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_e2e import ROOT, free_port


def certificates(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LectureVault SMTP acceptance only')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('mailpit')]), critical=False).sign(key, hashes.SHA256()))
    (directory / 'smtp.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def main():
    directory = ROOT / 'test-results' / f'https-{os.getpid()}'
    directory.mkdir(parents=True)
    private_key = certificates(directory)
    port, mail_port = free_port(), free_port()
    origin = f'https://vault.test:{port}'
    caddy = (ROOT / 'deploy/Caddyfile').read_text().replace('    encode zstd gzip', '    tls internal\n    encode zstd gzip')
    (directory / 'Caddyfile').write_text(caddy)
    environment = {**os.environ, 'LV_DOMAIN': 'vault.test', 'LV_ACME_EMAIL': 'operator@example.com',
                   'LV_SUPPORT_EMAIL': 'support@example.com', 'LV_SMTP_HOST': 'mailpit', 'LV_SMTP_PORT': '1025',
                   'LV_SMTP_TLS': 'starttls', 'LV_SMTP_USER': 'smtp-acceptance', 'LV_MAIL_FROM': 'noreply@example.com'}
    compose = f'''services:
  api:
    environment:
      LV_PUBLIC_URL: {origin}
    volumes:
      - smtp-test:/backups
  mailer:
    environment:
      LV_PUBLIC_URL: {origin}
      SSL_CERT_FILE: /test-certs/smtp.crt
    volumes:
      - {directory}:/test-certs:ro
  edge:
    ports: !override ["127.0.0.1:{port}:8443"]
    volumes:
      - {directory / 'Caddyfile'}:/etc/caddy/Caddyfile:ro
  mailpit:
    ports: !override ["127.0.0.1:{mail_port}:8025"]
    environment:
      MP_SMTP_TLS_CERT: /test-certs/smtp.crt
      MP_SMTP_TLS_KEY: /test-certs/smtp.key
      MP_SMTP_REQUIRE_STARTTLS: "true"
      MP_SMTP_AUTH_ACCEPT_ANY: "true"
      MP_DISABLE_VERSION_CHECK: "true"
    volumes:
      - smtp-test:/test-certs:ro
volumes:
  smtp-test:
'''
    (directory / 'compose.yaml').write_text(compose)
    command = ['docker', 'compose', '--profile', 'development', '-p', f'lecturevault-https-{os.getpid()}', '-f', str(ROOT/'compose.yaml'), '-f', str(ROOT/'compose.prod.yaml'), '-f', str(directory/'compose.yaml')]
    def run(args, **kwargs):
        return subprocess.run([*command, *args], cwd=ROOT, env=environment, check=True, **kwargs)
    try:
        with (directory/'compose.log').open('w') as output:
            run(['build', 'api', 'mailer', 'web', 'edge'], stdout=output, stderr=subprocess.STDOUT)
            # Synthetic SMTP secret only, written as the real application UID.
            setup = "import json,sys; from pathlib import Path; from backend.db import initialize; from backend.config import Settings; s=Settings(); initialize(s); p=s.secrets/'auth'/'smtp-password'; p.write_text('Synthetic SMTP acceptance password'); p.chmod(0o600); body=json.load(sys.stdin); Path('/backups/smtp.crt').write_text(body['cert']); key=Path('/backups/smtp.key'); key.write_text(body['key']); key.chmod(0o600)"
            run(['run', '--rm', '--no-deps', '-T', 'api', 'python', '-c', setup], input=json.dumps({'cert':(directory/'smtp.crt').read_text(),'key':private_key}).encode(), stdout=output, stderr=subprocess.STDOUT)
            run(['run', '--rm', '--no-deps', '-T', 'api', 'python', '-m', 'backend.manage', 'check-config'], stdout=output, stderr=subprocess.STDOUT)
            run(['up', '-d', '--wait', '--wait-timeout', '180', 'api', 'mailer', 'web', 'mailpit', 'edge'], stdout=output, stderr=subprocess.STDOUT)
        javascript = r'''
import { chromium, expect, request } from '@playwright/test';
import { readdirSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
const cache = join(homedir(), '.cache/ms-playwright');
const executablePath = process.env.CHROMIUM_PATH || readdirSync(cache).filter(n => /^chromium-\d+$/.test(n)).sort().reverse().flatMap(n => [join(cache,n,'chrome-linux64/chrome'),join(cache,n,'chrome-linux/chrome')]).find(existsSync);
const browser = await chromium.launch({executablePath, args:['--host-resolver-rules=MAP vault.test 127.0.0.1', '--no-proxy-server']});
try {
 const context = await browser.newContext({ignoreHTTPSErrors:true, viewport:{width:1440,height:1000}, locale:'ru-RU'});
 const page = await context.newPage();
 page.on('requestfailed', r=>console.log('requestfailed', r.method(),new URL(r.url()).pathname,r.failure()?.errorText));
 const email = `https-${Date.now()}@example.com`;
 for(let attempt=0; ; attempt++) {
   try { await page.goto(process.env.HTTPS_ORIGIN+'/register'); break; }
   catch(error) {
     if(attempt>=4 || !/ERR_NETWORK_CHANGED|ERR_CONNECTION_REFUSED|ERR_CONNECTION_CLOSED/.test(String(error))) throw error;
     await page.waitForTimeout(500);
   }
 }
 await page.getByLabel('Имя',{exact:true}).fill('HTTPS acceptance');
 await page.getByLabel('Email',{exact:true}).fill(email);
 await page.getByLabel('Пароль',{exact:true}).fill('A secure HTTPS acceptance password');
 await page.getByLabel('Повторите пароль').fill('A secure HTTPS acceptance password');
 await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Подтвердите email',exact:true})).toBeVisible();
 const mailbox = await request.newContext(); let link;
 await expect.poll(async()=>{
   const list = await (await mailbox.get(process.env.MAIL_ORIGIN+'/api/v1/messages')).json();
   for(const m of list.messages??[]) if(m.To.some(t=>t.Address===email)) {
     const body = await (await mailbox.get(process.env.MAIL_ORIGIN+'/api/v1/message/'+m.ID)).json();
     link = body.Text.match(/https:\/\/[^\s]+\/verify-email#token=[A-Za-z0-9_-]+/)?.[0];
     if(link) return true;
   } return false;
 },{timeout:30000}).toBe(true);
 expect(link.startsWith(process.env.HTTPS_ORIGIN+'/')).toBe(true);
 await page.goto(link); await page.getByRole('button',{name:'Подтвердить email',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Нет курсов',exact:true})).toBeVisible();
 const cookie = (await context.cookies()).find(c=>c.name==='__Host-lv_session');
 expect(cookie?.secure).toBe(true); expect(cookie?.httpOnly).toBe(true); expect(cookie?.sameSite).toBe('Strict'); expect(cookie?.path).toBe('/');
 const status = await page.evaluate(async()=>{
   const opts={method:'POST',headers:{'X-LectureVault':'1','Content-Type':'application/json'}};
   const course=await fetch('/api/courses',{...opts,body:JSON.stringify({title:'HTTPS course',workspace:'personal'})});
   if(course.status!==201) throw new Error(await course.text());
   const cid=(await course.json()).id;
   const response=await fetch('/api/notes',{...opts,body:JSON.stringify({courseId:cid})});
   const n=await response.json();
   const save=await fetch('/api/notes/'+n.id,{...opts,method:'PATCH',body:JSON.stringify({title:'HTTPS note',markdown:'## TLS and SMTP verified',revision:n.revision})});
   return {status:save.status,id:n.id};
 });
 expect(status.status).toBe(200);
 await page.goto(process.env.HTTPS_ORIGIN+'/notes/'+status.id);
 await expect(page.locator('.quartz-reader article')).toContainText('TLS and SMTP verified');
 await page.reload(); await expect(page.locator('.quartz-reader article')).toContainText('TLS and SMTP verified');
 await page.screenshot({path:process.env.HTTPS_SHOT,animations:'disabled'});
 const headers=await page.evaluate(async()=>Object.fromEntries((await fetch('/api/health')).headers));
 expect(headers['strict-transport-security']).toContain('max-age=31536000');
 expect(headers['content-security-policy']).toContain("frame-ancestors 'none'");
 const oversized = await page.evaluate(async()=>{
   const stream = new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(JSON.stringify({name:'x'.repeat(7*1024*1024)})));controller.close();}});
   return (await fetch('/api/auth/profile',{method:'PATCH',headers:{'X-LectureVault':'1','Content-Type':'application/json'},body:stream,duplex:'half'})).status;
 });
 expect(oversized).toBe(413);  // Ingress also bounds chunked JSON before API parsing.
 await page.evaluate(()=>fetch('/api/auth/logout',{method:'POST',headers:{'X-LectureVault':'1'}}));
 await page.reload(); await expect(page.getByRole('heading',{name:'Вход',exact:true})).toBeVisible();
 console.log('PASS: production overlay, Caddy HTTPS, authenticated STARTTLS SMTP, registration/verification, Secure HttpOnly __Host cookie, manual note/save/reload/logout, HSTS/CSP. Local test certificates only.');
 await mailbox.dispose(); await context.close();
} finally { await browser.close(); }
'''
        script = directory/'browser.mjs'; script.write_text(javascript)
        result = subprocess.call(['node', str(script)], cwd=ROOT, env={**environment,'HTTPS_ORIGIN':origin,'MAIL_ORIGIN':f'http://127.0.0.1:{mail_port}','HTTPS_SHOT':str(directory/'reader-https.png')})
        if result:
            return result
        with (directory/'compose.log').open('a') as output:
            run(['stop','api','mailer'],stdout=output,stderr=subprocess.STDOUT)
            run(['run','--rm','-T','--no-deps','-e','TMPDIR=/backups','api','python','-m','backend.manage','backup','--output','/backups/snapshot.tar.gz'],stdout=output,stderr=subprocess.STDOUT)
            restored = ['run','--rm','-T','--no-deps','-e','TMPDIR=/backups','-e','LV_DATA=/backups/restored/data','-e','LV_JOBS=/backups/restored/jobs','-e','LV_SECRETS=/backups/restored/secrets','api','python']
            run([*restored,'-m','backend.manage','restore','--input','/backups/snapshot.tar.gz'],stdout=output,stderr=subprocess.STDOUT)
            verification = "import os; from backend.config import Settings; from backend.db import connect; s=Settings(); c=connect(s); db=c.__enter__(); assert os.geteuid()==10001; assert db.execute('SELECT markdown FROM notes').fetchone()[0]=='## TLS and SMTP verified'; assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0; assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; c.__exit__(None,None,None)"
            run([*restored,'-c',verification],stdout=output,stderr=subprocess.STDOUT)
        print('PASS: non-root operator backup and restore preserve the note and remove sessions in new empty storage.')
        return 0
    except subprocess.CalledProcessError:
        print(f'HTTPS acceptance failed. See {directory}/compose.log', file=sys.stderr)
        return 1
    finally:
        subprocess.run([*command,'down','--volumes','--rmi','local'],cwd=ROOT,env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
