// Каталог воспроизводимых проверок. Здесь нет игровых вычислений.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const CASES=Object.freeze({
    'water-swamp':{version:1,profile:'effects',title:'Сахар и болотная вода',source:'Каталог продуктов · Сахар и Болотная вода',
        rule:'Вода-компонент сначала добавляет радиацию и истощение. Затем применяется основной эффект сахара.',card:'https://trello.com/c/wo26Noth',
        expected:['Два использования болотной воды: +20 радиации и +4 истощения; сахар затем убирает 3 истощения.',
            'Из исходных нулевых значений получаются 20 радиации и 1 истощения; стресс 2 → 1.',
            'В бутылке остаётся 1 использование, у сахара — 2.']},
    'water-rain':{version:1,profile:'effects',title:'Сахар и дождевая вода',source:'Каталог продуктов · Сахар и Дождевая вода',
        rule:'Вода-компонент сначала добавляет радиацию и истощение. Затем применяется основной эффект сахара.',card:'https://trello.com/c/wo26Noth',
        expected:['Два использования дождевой воды добавляют 10 радиации и 2 истощения до эффекта сахара.',
            'Итог: 10 радиации, 0 истощения, стресс 2 → 1.',
            'В бутылке остаётся 1 использование, у сахара — 2.']},
    'water-radioactive':{version:1,profile:'effects',title:'Сахар и радиоактивная вода',source:'Каталог продуктов · Сахар и Радиоактивная вода',
        rule:'Вода-компонент сначала добавляет радиацию и истощение. Затем применяется основной эффект сахара.',card:'https://trello.com/c/wo26Noth',
        expected:['Два использования радиоактивной воды добавляют 6 радиации и 1 истощения до эффекта сахара.',
            'Итог: 6 радиации, 0 истощения, стресс 2 → 1.',
            'В бутылке остаётся 1 использование, у сахара — 2.']},
    'pepper-head':{version:1,profile:'effects',title:'Перцовка: попадания в голову',source:'Правило автора · Боеприпасы со свойством «Перцовка»',
        rule:'Полное пробитие головы: пропуск хода, −5 к броскам до конца боя и боль каждый ход; после трёх попаданий — шок на 6 часов и штрафы на сутки.',
        card:'https://trello.com/c/wo26Noth',expected:['Первое и второе попадания учитываются как 1/3 и 2/3; бросок шока 99 не вызывает случайный шок.',
            'У цели появляются −5 к броскам; её следующий ход пропускается и добавляет боль.',
            'Третье попадание без броска вызывает шок на 6 часов и штраф к атаке/Внимательности на 24 часа.']},
    'belt-tools':{version:1,profile:'equipment',title:'Пояс: четыре инструмента',
        rule:'Крепежи для инструментов: до четырёх инструментов на поясе, их масса не учитывается.',
        source:'Создание.xlsx · Пояс · Крепежи для инструментов',card:'https://trello.com/c/zCVR4OYe',
        expected:['Четыре отдельных инструмента; остаток стопки остаётся в карманах.',
            'На надетом поясе масса инструментов равна нулю; заряды сохраняются.',
            'Крепление в бою стоит 3 ОД; пятое крепление недоступно.',
            'Снятие и повторное надевание пояса сохраняют инструменты. Снятый инструмент возвращается в карманы.']},
    'belt-light':{version:1,profile:'equipment',title:'Пояс: фонарь и ХИС',
        rule:'Крепёж для освещения позволяет носить один источник света на поясе.',
        source:'Создание.xlsx · Пояс · Крепеж для освещения',card:'https://trello.com/c/zCVR4OYe',
        expected:['Перенос включённого фонаря из руки на пояс стоит 3 ОД и освобождает руку.',
            'Настоящий луч опускается на высоту пояса; двуручное оружие доступно.',
            'ХИС продолжает гореть без перезапуска 30-минутного таймера.',
            'Кнопки и оба источника доступны на четырёх размерах настольного окна.']},
    'belt-artifact':{version:2,profile:'equipment',title:'Пояс: открытый артефакт',
        source:'Создание.xlsx · Пояс · Крепление для артефакта',card:'https://trello.com/c/zCVR4OYe',
        rule:'На поясе действует один артефакт со своими положительными и отрицательными свойствами, без защиты специального контейнера.',
        expected:['На крепление помещается один физический артефакт; масса учитывается.',
            'Контрольный артефакт даёт +15% физической защиты и +2 штрафа перемещения, оба свойства участвуют в серверном профиле.',
            'Крепление и снятие в бою стоят по 3 ОД. После снятия свойства перестают действовать.',
            'Лист показывает полученный с сервера штраф перемещения.',
            'Два боевых шага не дают радиацию. На 594-й секунде её 0, на 600-й — 3, следующий интервал начинается заново.']},
});

function codeVersion(){
    const names=[];
    function walk(directory){
        for(const entry of fs.readdirSync(path.join(root,directory),{withFileTypes:true})){
            const name=path.posix.join(directory,entry.name);
            if(entry.isSymbolicLink())continue;
            if(entry.isDirectory()){if(!['__pycache__','node_modules'].includes(entry.name))walk(name);}
            else if(/\.(py|js|cjs|css|json)$/.test(name))names.push(name);
        }
    }
    // Любая правка игрового кода требует пересмотра; ассеты и секреты не читаются.
    walk('app');walk('tests/helpers');
    names.push('tests/belt_attachments.browser.cjs','tests/belt_light.browser.cjs','tests/equipment_modifications.browser.cjs',
        'tests/world_object_editor.browser.cjs','tests/water_component.browser.cjs','tests/pepper_playtest.browser.cjs','tests/belt_artifact.browser.cjs','tmp/pripyat-regression.json');
    const hash=crypto.createHash('sha256');
    for(const name of [...new Set(names)].sort()){
        hash.update(name+'\0');hash.update(fs.readFileSync(path.join(root,name)));hash.update('\0');
    }
    return hash.digest('hex');
}

function evidenceWriter(session,caseId){
    const entry=CASES[caseId];
    if(!entry||!session?.verified||process.env.VTT_SAFE_SCENARIO!==entry.profile)
        throw Error('Нет допуска к записи фактического плейтеста');
    const version=codeVersion(),steps=[];
    return {
        step(title,before,after){steps.push({title,before,after});},
        publish({images=[],limitations=[],controls}={}){
            if(!steps.length)throw Error('Нет фактических шагов плейтеста');
            if(images.some(name=>!/^[-\w]+\.png$/.test(name)||!fs.existsSync(path.join(session.artifacts,name))))throw Error('Нет снимка плейтеста');
            const record={schema:1,caseId,caseVersion:entry.version,profile:entry.profile,codeVersion:version,
                recordedAt:new Date().toISOString(),scene:'Изолированная копия Припяти',
                roles:['ГМ, управляющий своим персонажем'],controls,steps,images,limitations,
                automaticResult:'passed',authorDecision:null};
            const folder=path.join(session.artifacts,'review-cases');fs.mkdirSync(folder,{recursive:true});
            fs.writeFileSync(path.join(folder,caseId+'.json'),JSON.stringify(record,null,2),'utf8');
        },
    };
}
module.exports={CASES,root,codeVersion,evidenceWriter};
