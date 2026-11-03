import os,pty,subprocess,sys,fcntl,termios,select,time,json,pathlib
import unittest

@unittest.skipUnless(sys.platform in ("darwin","linux"),"macOS/Linux installer")
class BootstrapPTYTests(unittest.TestCase):
    def test_piped_stdin_uses_controlling_tty_and_never_echoes_key(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        master,slave=pty.openpty()
        def setup():
         os.setsid();fcntl.ioctl(1,termios.TIOCSCTTY,0)
        code="import hashlib;import sys;sys.path.insert(0,'bunny');import cli; k=cli.ask('Dummy account key',hidden=True);r=cli.ask('Region','AMS');assert hashlib.sha256(k.encode()).hexdigest()=='bcde033fec76e1082e17b57bb79290f99ddc5bd3a93e494031d1f5a32ca95d58';assert r=='AMS';print('PTY_PROMPTS_OK')"
        p=subprocess.Popen([sys.executable,'-c',code],cwd=root,stdin=subprocess.PIPE,stdout=slave,stderr=slave,preexec_fn=setup)
        os.close(slave);p.stdin.close();output=b'';sent_key=False;sent_region=False;deadline=time.monotonic()+10
        while time.monotonic()<deadline:
         if select.select([master],[],[],0.1)[0]:
          try:chunk=os.read(master,4096)
          except OSError:break
          if not chunk:break
          output+=chunk
          if b'Dummy account key: ' in output and not sent_key:os.write(master,b'dummy-pty-secret\n');sent_key=True
          if b'Region [AMS]: ' in output and not sent_region:os.write(master,b'AMS\n');sent_region=True
         if p.poll() is not None:break
        os.close(master)
        try:p.wait(timeout=2)
        except subprocess.TimeoutExpired:p.kill();p.wait()
        report={'exit_code':p.returncode,'piped_stdin':True,'hidden_value_not_echoed':b'dummy-pty-secret' not in output,'ordinary_prompt_completed':b'PTY_PROMPTS_OK' in output.replace(b'\r',b'').splitlines()}
        assert p.returncode==0 and report['hidden_value_not_echoed'] and report['ordinary_prompt_completed']

if __name__=="__main__":unittest.main()
