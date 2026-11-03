"""THW Küchenlogistik API: Postgres, per-user sessions, admin-managed templates."""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, create_engine, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session, sessionmaker

DB_URL = os.environ.get('DATABASE_URL', 'sqlite:///./dev.db')
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
SESSION_DAYS = int(os.environ.get('SESSION_DAYS', '7'))
engine = create_engine(DB_URL, pool_pre_ping=True, connect_args={'check_same_thread': False} if DB_URL.startswith('sqlite') else {})
SessionLocal = sessionmaker(engine, expire_on_commit=False)
ph = PasswordHasher()

class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default='user')
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class LoginSession(Base):
    __tablename__ = 'login_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class Category(Base):
    __tablename__ = 'categories'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    position: Mapped[int] = mapped_column(Integer, default=0)
    prep_only: Mapped[bool] = mapped_column(Boolean, default=False)

class TemplateItem(Base):
    __tablename__ = 'template_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey('categories.id', ondelete='CASCADE'))
    label: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class Event(Base):
    __tablename__ = 'events'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    date: Mapped[str] = mapped_column(String(10))
    people: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str] = mapped_column(String(120), default='')
    location: Mapped[str] = mapped_column(String(180), default='')
    type: Mapped[str] = mapped_column(String(20), default='Ausgabe')
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    doc: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    items: Mapped[list['EventItem']] = relationship(back_populates='event', cascade='all, delete-orphan', order_by='EventItem.id')

class EventItem(Base):
    __tablename__ = 'event_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey('events.id', ondelete='CASCADE'))
    category: Mapped[str] = mapped_column(String(150))
    label: Mapped[str] = mapped_column(String(200))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[str] = mapped_column(String(100), default='')
    note: Mapped[str] = mapped_column(String(500), default='')
    custom: Mapped[bool] = mapped_column(Boolean, default=False)
    event: Mapped[Event] = relationship(back_populates='items')

class Audit(Base):
    __tablename__ = 'audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=500)
class UserCreate(Credentials):
    role: Literal['admin','user'] = 'user'
class UserUpdate(BaseModel):
    role: Literal['admin','user'] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=500)
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    prep_only: bool = False
    position: int = 0
class TemplateIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    category_id: int
    enabled: bool = True
    position: int = 0
class EventIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    people: int = Field(default=0, ge=0, le=100000)
    owner: str = Field(default='', max_length=120)
    location: str = Field(default='', max_length=180)
    type: Literal['Ausgabe', 'Zubereitung'] = 'Ausgabe'
class EventPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    date: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    people: int | None = Field(default=None, ge=0, le=100000)
    owner: str | None = Field(default=None, max_length=120)
    location: str | None = Field(default=None, max_length=180)
    type: Literal['Ausgabe', 'Zubereitung'] | None = None
    finished: bool | None = None
class ItemIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=150)
class ItemPatch(BaseModel):
    done: bool | None = None
    quantity: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)
class Documentation(BaseModel):
    planned: str = Field(default='', max_length=100)
    served: str = Field(default='', max_length=100)
    vegetarian: str = Field(default='', max_length=100)
    lead: str = Field(default='', max_length=120)
    intakeTemp: str = Field(default='', max_length=100)
    servingTemp: str = Field(default='', max_length=100)
    menu: str = Field(default='', max_length=10000)
    allergens: str = Field(default='', max_length=10000)
    hygiene: str = Field(default='', max_length=10000)
    receipts: str = Field(default='', max_length=10000)

app = FastAPI(title='THW OV Passau Küchenlogistik', docs_url=None, redoc_url=None)

def db():
    with SessionLocal() as s: yield s

def stamp(raw): return hashlib.sha256(raw.encode()).hexdigest()
def log(s, user, action, entity): s.add(Audit(actor=user.username, action=action, entity=entity))
def public_user(u): return {'id':u.id,'username':u.username,'role':u.role,'active':u.active}
def public_category(c): return {'id':c.id,'name':c.name,'position':c.position,'prep_only':c.prep_only}
def public_template(t): return {'id':t.id,'category_id':t.category_id,'label':t.label,'enabled':t.enabled,'position':t.position}
def public_item(i): return {'id':i.id,'category':i.category,'label':i.label,'done':i.done,'quantity':i.quantity,'note':i.note,'custom':i.custom}
def public_event(e, detail=False):
    obj={'id':e.id,'name':e.name,'date':e.date,'people':e.people,'owner':e.owner,'location':e.location,'type':e.type,'finished':e.finished,'done_count':sum(i.done for i in e.items),'item_count':len(e.items)}
    if detail: obj.update(items=[public_item(i) for i in e.items],doc=e.doc or {})
    return obj

def auth(request: Request, s: Session=Depends(db)):
    raw=request.cookies.get('thw_session','')
    if not raw: raise HTTPException(401,'Anmeldung erforderlich')
    sess=s.scalar(select(LoginSession).where(LoginSession.token_hash==stamp(raw)))
    now=datetime.now(timezone.utc)
    if not sess or sess.expires.replace(tzinfo=timezone.utc) <= now: raise HTTPException(401,'Sitzung abgelaufen')
    u=s.get(User,sess.user_id)
    if not u or not u.active: raise HTTPException(401,'Benutzer deaktiviert')
    if request.method not in ('GET','HEAD','OPTIONS'):
        if request.headers.get('X-CSRF-Token') != sess.csrf: raise HTTPException(403,'CSRF-Prüfung fehlgeschlagen')
        origin=request.headers.get('origin')
        if origin:
            from urllib.parse import urlparse
            if urlparse(origin).netloc != request.headers.get('host'): raise HTTPException(403,'Ungültiger Origin')
    return u

def admin(u: User=Depends(auth)):
    if u.role!='admin': raise HTTPException(403,'Nur Administratoren')
    return u

def must_event(s,id):
    e=s.get(Event,id)
    if not e: raise HTTPException(404,'Termin nicht gefunden')
    return e

def must_item(s,id):
    i=s.get(EventItem,id)
    if not i: raise HTTPException(404,'Prüfpunkt nicht gefunden')
    return i

def confirm_date(date):
    try: datetime.strptime(date,'%Y-%m-%d')
    except ValueError: raise HTTPException(422,'Ungültiges Datum')

@app.on_event('startup')
def startup():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        if not s.scalar(select(func.count()).select_from(User)):
            name=os.environ.get('ADMIN_USERNAME','admin').strip()
            password=os.environ.get('ADMIN_PASSWORD','')
            if len(password)<12: raise RuntimeError('ADMIN_PASSWORD muss für den ersten Start mindestens 12 Zeichen haben.')
            s.add(User(username=name,password_hash=ph.hash(password),role='admin'))
        if not s.scalar(select(func.count()).select_from(Category)):
            rows=json.loads((Path(__file__).parent/'defaults.json').read_text(encoding='utf-8'))
            for ix,(name,labels) in enumerate(rows):
                c=Category(name=name,position=ix,prep_only=name=='Zubereitung vor Ort')
                s.add(c); s.flush()
                for j,label in enumerate(labels): s.add(TemplateItem(category_id=c.id,label=label,position=j))
        s.commit()

@app.post('/api/auth/login')
def login(data: Credentials, response: Response, s: Session=Depends(db)):
    u=s.scalar(select(User).where(User.username==data.username))
    try: valid=bool(u and ph.verify(u.password_hash,data.password))
    except (VerifyMismatchError,VerificationError): valid=False
    if not valid or not u.active: raise HTTPException(401,'Benutzername oder Passwort falsch')
    raw=secrets.token_urlsafe(48); csrf=secrets.token_urlsafe(32)
    s.add(LoginSession(token_hash=stamp(raw),csrf=csrf,user_id=u.id,expires=datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)))
    log(s,u,'login','session'); s.commit()
    response.set_cookie('thw_session',raw,httponly=True,secure=COOKIE_SECURE,samesite='lax',max_age=SESSION_DAYS*86400,path='/')
    return {'user':public_user(u),'csrf':csrf}

@app.get('/api/auth/me')
def me(request: Request, u: User=Depends(auth), s: Session=Depends(db)):
    sess=s.scalar(select(LoginSession).where(LoginSession.token_hash==stamp(request.cookies['thw_session'])))
    return {'user':public_user(u),'csrf':sess.csrf}

@app.post('/api/auth/logout')
def logout(request:Request,response:Response,u:User=Depends(auth),s:Session=Depends(db)):
    sess=s.scalar(select(LoginSession).where(LoginSession.token_hash==stamp(request.cookies['thw_session'])))
    s.delete(sess); log(s,u,'logout','session'); s.commit();response.delete_cookie('thw_session',path='/');return {'ok':True}

@app.get('/api/users')
def list_users(u:User=Depends(admin),s:Session=Depends(db)):
    return [public_user(x) for x in s.scalars(select(User).order_by(User.username))]
@app.post('/api/users',status_code=201)
def create_user(data:UserCreate,u:User=Depends(admin),s:Session=Depends(db)):
    if len(data.password)<12: raise HTTPException(422,'Passwort mindestens 12 Zeichen')
    if s.scalar(select(User).where(User.username==data.username)): raise HTTPException(409,'Benutzer existiert bereits')
    x=User(username=data.username,password_hash=ph.hash(data.password),role=data.role)
    s.add(x);log(s,u,'user.create',data.username);s.commit();return public_user(x)
@app.patch('/api/users/{id}')
def update_user(id:int,data:UserUpdate,u:User=Depends(admin),s:Session=Depends(db)):
    x=s.get(User,id)
    if not x: raise HTTPException(404,'Benutzer nicht gefunden')
    values=data.model_dump(exclude_unset=True)
    if x.id==u.id and (values.get('active') is False or values.get('role')=='user'): raise HTTPException(409,'Eigenen Adminzugriff nicht entfernen')
    if ('active' in values and not values['active']) or values.get('role')=='user':
        admins=s.scalars(select(User).where(User.role=='admin',User.active==True)).all()
        if x.role=='admin' and x.active and len(admins)<=1: raise HTTPException(409,'Letzter aktiver Admin muss erhalten bleiben')
    if 'password' in values: values['password_hash']=ph.hash(values.pop('password'))
    for k,v in values.items(): setattr(x,k,v)
    if 'active' in values and not x.active:
        for session in s.scalars(select(LoginSession).where(LoginSession.user_id==id)): s.delete(session)
    log(s,u,'user.update',x.username);s.commit();return public_user(x)

@app.get('/api/categories')
def categories(u:User=Depends(auth),s:Session=Depends(db)):
    return [public_category(x) for x in s.scalars(select(Category).order_by(Category.position,Category.id))]
@app.post('/api/categories',status_code=201)
def create_category(data:CategoryIn,u:User=Depends(admin),s:Session=Depends(db)):
    c=Category(**data.model_dump());s.add(c);s.flush();log(s,u,'category.create',str(c.id));s.commit();return public_category(c)
@app.patch('/api/categories/{id}')
def edit_category(id:int,data:CategoryIn,u:User=Depends(admin),s:Session=Depends(db)):
    c=s.get(Category,id)
    if not c: raise HTTPException(404,'Kategorie nicht gefunden')
    for k,v in data.model_dump().items(): setattr(c,k,v)
    log(s,u,'category.update',str(id));s.commit();return public_category(c)
@app.delete('/api/categories/{id}')
def delete_category(id:int,u:User=Depends(admin),s:Session=Depends(db)):
    c=s.get(Category,id)
    if not c: raise HTTPException(404,'Kategorie nicht gefunden')
    for t in s.scalars(select(TemplateItem).where(TemplateItem.category_id==id)): s.delete(t)
    s.delete(c);log(s,u,'category.delete',str(id));s.commit();return {'ok':True}
@app.get('/api/templates')
def templates(u:User=Depends(auth),s:Session=Depends(db)):
    return [public_template(x) for x in s.scalars(select(TemplateItem).order_by(TemplateItem.category_id,TemplateItem.position,TemplateItem.id))]
@app.post('/api/templates',status_code=201)
def create_template(data:TemplateIn,u:User=Depends(admin),s:Session=Depends(db)):
    if not s.get(Category,data.category_id): raise HTTPException(404,'Kategorie nicht gefunden')
    t=TemplateItem(**data.model_dump());s.add(t);s.flush();log(s,u,'template.create',str(t.id));s.commit();return public_template(t)
@app.patch('/api/templates/{id}')
def edit_template(id:int,data:TemplateIn,u:User=Depends(admin),s:Session=Depends(db)):
    t=s.get(TemplateItem,id)
    if not t: raise HTTPException(404,'Vorlage nicht gefunden')
    if not s.get(Category,data.category_id): raise HTTPException(404,'Kategorie nicht gefunden')
    for k,v in data.model_dump().items(): setattr(t,k,v)
    log(s,u,'template.update',str(id));s.commit();return public_template(t)
@app.delete('/api/templates/{id}')
def delete_template(id:int,u:User=Depends(admin),s:Session=Depends(db)):
    t=s.get(TemplateItem,id)
    if not t: raise HTTPException(404,'Vorlage nicht gefunden')
    s.delete(t);log(s,u,'template.delete',str(id));s.commit();return {'ok':True}

@app.get('/api/events')
def events(u:User=Depends(auth),s:Session=Depends(db)):
    return [public_event(e) for e in s.scalars(select(Event).order_by(Event.date.desc(),Event.id.desc()))]
@app.post('/api/events',status_code=201)
def create_event(data:EventIn,u:User=Depends(auth),s:Session=Depends(db)):
    confirm_date(data.date)
    e=Event(**data.model_dump(),created_by=u.id,doc={})
    s.add(e);s.flush()
    cat_list=s.scalars(select(Category).order_by(Category.position,Category.id)).all()
    for c in cat_list:
        if c.prep_only and e.type=='Ausgabe':continue
        for t in s.scalars(select(TemplateItem).where(TemplateItem.category_id==c.id,TemplateItem.enabled==True).order_by(TemplateItem.position,TemplateItem.id)):
            s.add(EventItem(event_id=e.id,category=c.name,label=t.label))
    log(s,u,'event.create',str(e.id));s.commit();s.refresh(e);return public_event(e,True)
@app.get('/api/events/{id}')
def event_detail(id:int,u:User=Depends(auth),s:Session=Depends(db)):
    return public_event(must_event(s,id),True)
@app.patch('/api/events/{id}')
def edit_event(id:int,data:EventPatch,u:User=Depends(auth),s:Session=Depends(db)):
    e=must_event(s,id)
    changes=data.model_dump(exclude_unset=True)
    if changes.get('date'): confirm_date(changes['date'])
    for k,v in changes.items():setattr(e,k,v)
    log(s,u,'event.update',str(id));s.commit();return public_event(e,True)
@app.delete('/api/events/{id}')
def delete_event(id:int,u:User=Depends(admin),s:Session=Depends(db)):
    e=must_event(s,id);s.delete(e);log(s,u,'event.delete',str(id));s.commit();return {'ok':True}
@app.post('/api/events/{id}/items',status_code=201)
def add_item(id:int,data:ItemIn,u:User=Depends(auth),s:Session=Depends(db)):
    must_event(s,id);i=EventItem(event_id=id,**data.model_dump(),custom=True);s.add(i);s.flush();log(s,u,'item.create',str(i.id));s.commit();return public_item(i)
@app.patch('/api/events/{id}/items/{item_id}')
def edit_item(id:int,item_id:int,data:ItemPatch,u:User=Depends(auth),s:Session=Depends(db)):
    must_event(s,id);i=must_item(s,item_id)
    if i.event_id != id:raise HTTPException(404,'Prüfpunkt nicht gefunden')
    for k,v in data.model_dump(exclude_unset=True).items():setattr(i,k,v)
    log(s,u,'item.update',str(i.id));s.commit();return public_item(i)
@app.delete('/api/events/{id}/items/{item_id}')
def delete_item(id:int,item_id:int,u:User=Depends(auth),s:Session=Depends(db)):
    must_event(s,id);i=must_item(s,item_id)
    if i.event_id != id or not i.custom:raise HTTPException(403,'Nur eigene Zusatzpunkte löschbar')
    s.delete(i);log(s,u,'item.delete',str(item_id));s.commit();return {'ok':True}
@app.put('/api/events/{id}/documentation')
def document_event(id:int,data:Documentation,u:User=Depends(auth),s:Session=Depends(db)):
    e=must_event(s,id);e.doc=data.model_dump();log(s,u,'documentation.update',str(id));s.commit();return e.doc
@app.get('/api/audit')
def audit(u:User=Depends(admin),s:Session=Depends(db)):
    return [{'at':a.at.isoformat(),'actor':a.actor,'action':a.action,'entity':a.entity} for a in s.scalars(select(Audit).order_by(Audit.id.desc()).limit(150))]
@app.get('/api/health')
def health():return {'status':'ok'}

FRONTEND=Path(__file__).parent.parent/'frontend'
app.mount('/assets',StaticFiles(directory=str(FRONTEND)),name='assets')
@app.get('/')
def index():return FileResponse(FRONTEND/'index.html')
