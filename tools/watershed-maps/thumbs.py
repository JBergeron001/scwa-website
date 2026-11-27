"""Render gallery thumbnails: python3 thumbs.py <base url> <site root> <geoid>..."""
import asyncio, sys, os
from playwright.async_api import async_playwright
from PIL import Image
base, root, ids = sys.argv[1], sys.argv[2], sys.argv[3:]
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path='/usr/bin/google-chrome',args=['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--ignore-gpu-blocklist'])
        pg=await b.new_page(viewport={'width':1300,'height':900})
        for g in ids:
            await pg.goto(f'{base}/watershed-map.html?c={g}'); await pg.wait_for_timeout(6000)
            await pg.evaluate("document.getElementById('spin').click()"); await pg.wait_for_timeout(400)
            await pg.add_style_tag(content='.tools,.cap,.tip,.lab{display:none!important}')
            out=os.path.join(root,'watershed-maps','thumbs',g+'.png')
            await pg.locator('#stage').screenshot(path=out)
            im=Image.open(out).convert('RGB'); w,h=im.size; tw=int(h*1.5)
            if tw<w: im=im.crop(((w-tw)//2,0,(w-tw)//2+tw,h))
            im.thumbnail((720,480)); im.save(out[:-4]+'.jpg',quality=82); os.remove(out); print('thumb',g)
        await b.close()
asyncio.run(main())
