"""Selenium/WebDriver provider with the same async AnyBridge capability surface."""
from __future__ import annotations
import asyncio, base64, time
from urllib.parse import urlsplit
from pathlib import Path
from .security import NetworkGuard
from .sites import normalize_url

_HERE=Path(__file__).parent
SHIM=(_HERE/"shim.js").read_text()
PAGETOOLS=(_HERE/"pagetools.js").read_text()

class SeleniumDependencyError(RuntimeError): pass

class SeleniumDriver:
    def __init__(self,url=None,headless=True,*,allow_private_network=True,storage_state=None,
                 allowed_hosts=(),browser="chrome",driver=None):
        self.url=normalize_url(url) if url else "about:blank"
        self.headless=headless; self.storage_state=storage_state
        self.browser=browser.casefold()
        self._guard=NetworkGuard(allow_private=allow_private_network,isolate_private=True,allowed_hosts=allowed_hosts)
        self._driver=driver; self._owns_driver=driver is None; self._started=False; self._injected=False

    def _import(self):
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options as ChromeOptions
            from selenium.webdriver.firefox.options import Options as FirefoxOptions
            from selenium.webdriver.edge.options import Options as EdgeOptions
            return webdriver,ChromeOptions,FirefoxOptions,EdgeOptions
        except ImportError as e:
            raise SeleniumDependencyError("Install anybridge[selenium] to use the Selenium provider.") from e

    def _create_driver(self):
        webdriver,ChromeOptions,FirefoxOptions,EdgeOptions=self._import()
        if self.browser=="chrome":
            o=ChromeOptions()
            if self.headless:o.add_argument("--headless=new")
            o.enable_bidi = True
            o.add_argument("--disable-background-networking");o.add_argument("--disable-component-update")
            o.add_argument("--disable-sync");o.add_argument("--no-first-run")
            return webdriver.Chrome(options=o)
        if self.browser=="firefox":
            o=FirefoxOptions()
            if self.headless:o.add_argument("-headless")
            o.enable_bidi = True
            return webdriver.Firefox(options=o)
        if self.browser=="edge":
            o=EdgeOptions()
            if self.headless:o.add_argument("--headless=new")
            o.enable_bidi = True
            return webdriver.Edge(options=o)
        raise ValueError(f"Unsupported Selenium browser: {self.browser!r}")

    async def start(self,settle=1.0):
        if self._started:return self
        self._driver=self._driver or await asyncio.to_thread(self._create_driver)
        self._install_bidi_network_guard()
        self._started=True
        if self.url!="about:blank":
            await self.navigate(self.url); await asyncio.sleep(settle)
        return self

    def _install_bidi_network_guard(self):
        """Intercept every WebDriver request when Selenium BiDi is available."""
        try:
            from selenium.webdriver.common.bidi.network import Network
            network = getattr(self._driver, "network", None)
            if network is None:
                return
            def before_request(request):
                try:
                    self._guard.check_url_sync(request.url)
                except Exception:
                    try:
                        request.fail_request()
                    except AttributeError:
                        request.fail()
                    host = (urlsplit(request.url).hostname or "").rstrip(".").casefold()
                    if host:
                        self._guard._blocked_hosts[host] = {
                            "host": host, "scheme": urlsplit(request.url).scheme, "reason": "network_policy_block"
                        }
            self._bidi_callback_id = network.add_request_handler("before_request", before_request)
        except (ImportError, AttributeError, Exception):
            # Selenium versions/browsers without BiDi retain navigation-level checks.
            self._bidi_callback_id = None

    async def close(self):
        if self._driver is not None and getattr(self, "_bidi_callback_id", None) is not None:
            try:
                self._driver.network.remove_request_handler("before_request", self._bidi_callback_id)
            except Exception:
                pass
        if self._driver is not None and self._owns_driver:
            await asyncio.to_thread(self._driver.quit)
        self._driver=None; self._started=False

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    @property
    def started(self):return self._started
    @property
    def current_url(self):return self._driver.current_url if self._driver else self.url
    async def _run(self,fn,*args):return await asyncio.to_thread(fn,*args)

    async def _inject(self):
        if self._injected:return
        await self._run(self._driver.execute_script,SHIM)
        await self._run(self._driver.execute_script,PAGETOOLS)
        self._injected=True

    async def navigate(self,url):
        url=await self._guard.assert_url(normalize_url(url))
        await self._run(self._driver.get,url); self._injected=False; await self._inject()
        return await self.current_site()

    async def read_page(self, selector=None, max_chars=20000, pages=None):
        return await self.snapshot(interactive_only=False, selector=selector, max_chars=max_chars)

    async def list_links(self, filter_text=None, limit=100):
        await self._inject()
        links=await self._run(self._driver.execute_script, "return [...document.querySelectorAll('a[href]')].map(a=>({text:(a.innerText||a.textContent||'').trim(),url:a.href}));")
        if filter_text:
            q=filter_text.casefold(); links=[x for x in links if q in x["text"].casefold() or q in x["url"].casefold()]
        return links[:limit]

    async def list_forms(self):
        await self._inject()
        return await self._run(self._driver.execute_script, "return [...document.forms].map((f,i)=>({index:i,action:f.action,method:f.method,fields:[...f.elements].map(e=>({name:e.name,type:e.type,value:e.value}))}));")

    async def click(self,target):
        return await self.click_text(target)

    async def type_text(self,target,text,press_enter=False):
        return await self.fill_label(target,text) if not press_enter else await self.fill_ref(target,text,press_enter=True)

    async def submit_form(self,index,fields):
        script="const f=document.forms[arguments[0]]; if(!f) throw new Error('Form not found'); for(const [k,v] of Object.entries(arguments[1])) { const e=f.elements[k]; if(e) e.value=v; } f.requestSubmit();"
        await self._run(self._driver.execute_script,script,index,fields)
        return await self.snapshot()

    async def discover_tools(self,timeout=30,reload_on_failure=False):
        await self._inject()
        return await self.list_tools()

    async def reset(self):
        await self.close()
        return "Selenium browser session reset."

    async def storage_snapshot(self,origin=None):
        cookies=await self._run(self._driver.get_cookies)
        storage=await self._run(self._driver.execute_script,"return {...localStorage};")
        return {"cookies":cookies,"origins":{origin or self.current_url:{"localStorage":storage,"sessionStorage":{}}}}

    async def load_storage_snapshot(self,state,target):
        await self.navigate(target)
        for cookie in state.get("cookies",[]):
            try: await self._run(self._driver.add_cookie,cookie)
            except Exception: pass
        return await self.current_site()

    def begin_recording(self):
        self._recording=[]; self._recording_start_url=self.current_url

    @property
    def recording_start_url(self):
        return getattr(self,"_recording_start_url",None)

    def end_recording(self):
        return getattr(self,"_recording",[])

    async def run_recorded_step(self,step,variables):
        action=step.get("action");target=step.get("target") or {}
        if action=="click": return await self.click_text(target.get("name") or target.get("text") or target.get("selector"))
        if action=="fill": return await self.fill_label(target.get("name") or target.get("text") or target.get("selector"),str(variables[step["variable"]]))
        if action=="press": return await self.press_key(step.get("key") or "ENTER")
        raise ValueError(f'Unsupported workflow action: {action!r}')

    async def current_site(self):
        if not self._started:return {}
        return await self._run(lambda:{"url":self._driver.current_url,"title":self._driver.title})

    async def snapshot(self,interactive_only=True,compact=True,selector=None,max_chars=12000):
        await self._inject()
        script="return arguments[0] ? (document.querySelector(arguments[0])?.innerText || '') : (document.body?.innerText || '');"
        return str(await self._run(self._driver.execute_script,script,selector) or "")[:max_chars]

    def _ref_css(self,ref):return f'[data-anybridge-ref="{ref.replace(chr(34),chr(92)+chr(34))}"]'

    async def click_ref(self,ref):
        e=await self._run(self._driver.find_element,"css selector",self._ref_css(ref));await self._run(e.click);return await self.snapshot()

    async def fill_ref(self,ref,value,press_enter=False):
        e=await self._run(self._driver.find_element,"css selector",self._ref_css(ref));await self._run(e.clear);await self._run(e.send_keys,value)
        if press_enter:
            from selenium.webdriver.common.keys import Keys
            await self._run(e.send_keys,Keys.ENTER)
        return await self.snapshot()

    async def select_ref(self,ref,value):
        from selenium.webdriver.support.ui import Select
        e=await self._run(self._driver.find_element,"css selector",self._ref_css(ref));await self._run(Select(e).select_by_visible_text,value);return await self.snapshot()

    async def press_key(self,key,ref=None):
        from selenium.webdriver.common.keys import Keys
        e=await self._run(self._driver.find_element,"css selector",self._ref_css(ref)) if ref else await self._run(lambda:self._driver.switch_to.active_element)
        await self._run(e.send_keys,getattr(Keys,key.upper(),key));return await self.snapshot()

    async def wait_for(self,selector=None,text=None,timeout_ms=10000):
        deadline=time.monotonic()+timeout_ms/1000
        while time.monotonic()<deadline:
            if selector and await self._run(self._driver.find_elements,"css selector",selector):return True
            if text and text in await self._run(lambda:self._driver.find_element("tag name","body").text):return True
            await asyncio.sleep(.1)
        raise TimeoutError("Timed out waiting for the requested browser condition.")

    async def extract_structured(self,schema,selector=None):
        await self._inject()
        value=await self._run(self._driver.execute_script,"const e=arguments[0]?document.querySelector(arguments[0]):document.body; return e?e.innerText:'';",selector)
        return {"text":str(value or ""), "schema":schema}

    async def screenshot(self,full_page=False):
        return base64.b64encode(await self._run(self._driver.get_screenshot_as_png)).decode("ascii")

    async def list_tools(self):
        await self._inject()
        return await self._run(self._driver.execute_script,"return window.__anybridge__?.listTools?.() || [];")

    async def call_tool(self,name,arguments):
        await self._inject()
        script="return window.__anybridge__.callTool(arguments[0],arguments[1]);"
        return await self._run(self._driver.execute_async_script,"const done=arguments[arguments.length-1]; Promise.resolve(window.__anybridge__.callTool(arguments[0],arguments[1])).then(done);",name,arguments)

    async def click_text(self, text):
        script = "return [...document.querySelectorAll('button,a,input,[role=button]')].find(e=>(e.innerText||e.value||e.getAttribute('aria-label')||'').trim().toLowerCase()===arguments[0].toLowerCase());"
        element = await self._run(self._driver.execute_script, script, text)
        if element is None:
            raise AssertionError(f"Element '{text}' was not found.")
        await self._run(element.click)
        return await self.snapshot()

    async def fill_label(self, label, value):
        script = "return [...document.querySelectorAll('input,textarea,[contenteditable=true]')].find(e=>(e.name||e.placeholder||e.getAttribute('aria-label')||'').trim().toLowerCase()===arguments[0].toLowerCase());"
        element = await self._run(self._driver.execute_script, script, label)
        if element is None:
            raise AssertionError(f"Field '{label}' was not found.")
        await self._run(element.clear)
        await self._run(element.send_keys, value)
        return await self.snapshot()

    async def capabilities(self):
        return {
            "provider": "selenium",
            "browser": self.browser,
            "webmcp": True,
            "network_interception": getattr(self, "_bidi_callback_id", None) is not None,
            "websocket_interception": False,
            "screenshot": True,
            "storage_state": True,
            "bdd": True,
        }

    async def collect_evidence(
        self,
        *,
        test_id=None,
        execution_id=None,
        action=None,
        expected=None,
        assertion=None,
        status="observed",
        confidence=None,
    ):
        from .evidence import EvidenceEnvelope
        started = time.perf_counter()
        snapshot = await self.snapshot(interactive_only=False, max_chars=20000)
        screenshot = await self.screenshot()
        policy = await self.network_policy()
        return EvidenceEnvelope(
            test_id=test_id,
            execution_id=execution_id,
            url=self.current_url,
            action=action,
            expected=expected,
            observed=snapshot,
            assertion=assertion,
            status=status,
            screenshot=screenshot,
            dom_snapshot=snapshot,
            network_errors=list(policy.get("blocked_hosts", [])),
            timing_ms=(time.perf_counter() - started) * 1000,
            browser=self.browser,
            provider="selenium",
            environment={"private_isolation": policy.get("private_site_isolation", False)},
            confidence=confidence,
        ).to_dict()

    async def network_policy(self):return self._guard.policy()
    async def trust_host(self,host):self._guard.allow_hosts((host,));return self._guard.policy()
    async def revoke_host(self,host):return self._guard.revoke_host(host)
