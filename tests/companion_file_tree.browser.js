async page => {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  await page.reload();
  await page.waitForSelector('#ft-root .ft-row');
  assert(await page.locator('#class-graph + #file-tree').count() === 1, 'Tree must follow graph');
  assert(await page.locator('#ft-root li[data-kind="file"]:visible').count() > 0, 'Files visible initially');
  assert(await page.locator('#ft-root li[data-kind="class"]:visible').count() === 0, 'Classes initially hidden');
  const file = page.locator('#ft-root li[data-kind="file"]').filter({hasText:'ConversationReadBudget'}).first();
  const fileToggle = file.locator(':scope > .ft-row > .ft-toggle');
  await fileToggle.focus();
  await page.keyboard.press('Enter');
  assert(await fileToggle.getAttribute('aria-expanded') === 'true', 'Keyboard opens file');
  const klass = file.locator('li[data-kind="class"]').filter({has:page.locator('button.ft-name', {hasText:/^ConversationReadBudget$/})}).first();
  const classToggle = klass.locator(':scope > .ft-row > .ft-toggle');
  assert(await klass.locator('li[data-kind="method"]:visible').count() === 0, 'Methods initially hidden');
  await classToggle.click();
  assert(await klass.locator('li[data-kind="method"]:visible').count() > 0, 'Arrow opens methods');
  await klass.locator(':scope > .ft-row > button.ft-name').click();
  assert(await page.locator('#cg-find').inputValue() === 'ConversationReadBudget', 'Class selected in graph');
  assert(await page.locator('#cg-view').inputValue() === 'focus', 'Graph explorer opened');
  assert(await page.locator('#cg-detail h3').textContent() === 'ConversationReadBudget', 'Correct details');
  assert(await page.evaluate(() => document.activeElement.dataset.id) === 'ConversationReadBudget', 'Focus transferred');
  assert(await classToggle.getAttribute('aria-expanded') === 'true', 'Class navigation does not toggle tree');
  await page.locator('#cg-back').click();
  await classToggle.focus();
  await page.keyboard.press('Space');
  assert(await classToggle.getAttribute('aria-expanded') === 'false', 'Keyboard closes class');
  const folderToggle = page.locator('#ft-root > li > .ft-row > .ft-toggle').first();
  await folderToggle.click();
  assert(await folderToggle.getAttribute('aria-expanded') === 'false', 'Folder collapses');
  await folderToggle.click();
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Page overflow at '+width);
    const box = await fileToggle.boundingBox();
    assert(box.width >= 44 && box.height >= 44, 'Accessible toggle target');
  }
  await page.reload();
  assert(await page.locator('#ft-root li[data-kind="class"]:visible').count() === 0, 'Reload restores file-level view');
  const source = await (await page.reload()).text();
  const files = [
    {path:'src/moved.py',previousPath:'old/reader.py',status:'Moved',symbols:[
      {name:'ConversationReadBudget',kind:'class',graphId:'ConversationReadBudget',status:'Changed',children:[
        {name:'read()',kind:'method',status:'Unchanged'}
      ]},
      {name:'<img src=x onerror=alert(1)>',kind:'function',status:'New'}
    ]},
    {path:'src/removed.py',status:'Deleted',symbols:[{name:'OldReader',kind:'class',status:'Deleted'}]},
    {path:'config.json',status:'Changed',symbols:[]}
  ];
  await page.setContent(source.replace("const root=document.querySelector('#class-graph')", "data.files="+JSON.stringify(files)+";\nconst root=document.querySelector('#class-graph')"));
  await page.waitForSelector('#ft-root .ft-row');
  assert(await page.locator('#ft-root li[data-kind="file"]').count() === 3, 'Explicit inventory includes non-class files');
  const moved = page.locator('#ft-root li[data-kind="file"]').filter({has:page.locator('.ft-name').filter({hasText:/^moved\.py$/})});
  await moved.locator(':scope > .ft-row > .ft-toggle').click();
  await moved.locator('li[data-kind="class"] > .ft-row > .ft-toggle').click();
  assert(await moved.locator('li[data-kind="method"] .ft-status').textContent() === 'Unchanged', 'Method status independent of file');
  assert(await moved.locator(':scope > .ft-row').textContent().then(t=>t.includes('old/reader.py')), 'Move origin visible');
  assert(await page.locator('#ft-root img').count() === 0, 'Names rendered as text');
  assert(await page.locator('#ft-root li[data-kind="function"] .ft-name').textContent() === '<img src=x onerror=alert(1)>', 'Top-level function retained');
  assert(await page.locator('#ft-root li[data-kind="class"] button.ft-name').count() === 1, 'Removed class has no invented graph link');
  await page.reload();
  return 'File tree browser checks passed';
}
