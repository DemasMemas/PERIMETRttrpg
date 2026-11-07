const node=(tag,text)=>{const el=document.createElement(tag);el.textContent=text;return el;};
export const bookTitle=name=>String(name||'').replace('?. Раздел:','? Раздел:');

export function createBookCard(entry,{skills,assignments,selectedSkills,learned=[],canEdit,busy,onSkillChange,onRead}){
    const attrs=entry.item.attributes||{},card=document.createElement('article');card.className='pda-file-card';
    card.append(node('strong',bookTitle(entry.item.name)));
    let skillPath=attrs.book_skill_path||assignments?.[entry.item.id]||null;
    if(attrs.book_kind==='skill'&&!skillPath){
        const subject=String(attrs.book_subject||'').toLowerCase(),group=subject.includes('физическ')?'physical':subject.includes('социальн')?'social':'other';
        const options=skills.filter(row=>row.path.startsWith(group+'.')),choices=document.createElement('div');choices.className='pda-file-skills';
        skillPath=selectedSkills.get(entry.item.id)||options[0]?.path;
        for(const row of options){const button=node('button',row.label);button.type='button';button.className=row.path===skillPath?'active':'';
            button.onclick=()=>{selectedSkills.set(entry.item.id,row.path);onSkillChange();};choices.append(button);}
        card.append(choices);
    }
    const completed=learned.some(book=>book.book_kind===attrs.book_kind&&book.book_subject===attrs.book_subject
        &&(attrs.book_kind!=='skill'||book.skill_path===skillPath));
    const read=node('button',completed?'Прочитано':'Читать · 4 часа');read.type='button';read.className='btn btn-secondary';
    read.disabled=completed||busy||!canEdit||attrs.book_kind==='skill'&&!skillPath;read.onclick=()=>onRead(skillPath);card.append(read);
    return card;
}
