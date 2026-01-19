--
-- PostgreSQL database dump
--

\restrict bQ9N63d7tkk17AglGJqbHCpScMowaMjCJNVSRSx9cqunk2SElpFXivjsNnctzJS

-- Dumped from database version 17.6 (Debian 17.6-2.pgdg12+1)
-- Dumped by pg_dump version 18.0

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: public; Type: SCHEMA; Schema: -; Owner: telegram_events_db_user
--

-- *not* creating schema, since initdb creates it


ALTER SCHEMA public OWNER TO telegram_events_db_user;

--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: telegram_events_db_user
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO telegram_events_db_user;

--
-- Name: chats; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.chats (
    id bigint NOT NULL,
    title text,
    is_forum boolean DEFAULT false
);


ALTER TABLE public.chats OWNER TO telegram_events_db_user;

--
-- Name: dashboard_login_log; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.dashboard_login_log (
    id integer NOT NULL,
    user_id integer NOT NULL,
    login_at timestamp with time zone DEFAULT now() NOT NULL,
    logout_at timestamp with time zone,
    ip_address character varying(64),
    user_agent text
);


ALTER TABLE public.dashboard_login_log OWNER TO telegram_events_db_user;

--
-- Name: dashboard_login_log_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.dashboard_login_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dashboard_login_log_id_seq OWNER TO telegram_events_db_user;

--
-- Name: dashboard_login_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.dashboard_login_log_id_seq OWNED BY public.dashboard_login_log.id;


--
-- Name: dashboard_users; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.dashboard_users (
    id integer NOT NULL,
    username character varying(50) NOT NULL,
    password_hash character varying(255) NOT NULL,
    is_admin boolean,
    is_active boolean,
    created_at timestamp without time zone,
    last_login_at timestamp without time zone,
    email character varying(255),
    first_name character varying(100),
    last_name character varying(100),
    login_count integer DEFAULT 0 NOT NULL,
    can_view_reagents boolean DEFAULT false NOT NULL
);


ALTER TABLE public.dashboard_users OWNER TO telegram_events_db_user;

--
-- Name: dashboard_users_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.dashboard_users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.dashboard_users_id_seq OWNER TO telegram_events_db_user;

--
-- Name: dashboard_users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.dashboard_users_id_seq OWNED BY public.dashboard_users.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.events (
    id bigint NOT NULL,
    chat_id bigint NOT NULL,
    user_id bigint NOT NULL,
    well text NOT NULL,
    event_type text NOT NULL,
    reagent text,
    qty double precision,
    p_tube double precision,
    p_line double precision,
    event_time timestamp without time zone NOT NULL,
    description text,
    lat double precision,
    lon double precision,
    created_at timestamp without time zone DEFAULT now(),
    equip_type text,
    equip_points text,
    equip_other text,
    purge_phase text,
    other_kind text,
    geo_status text
);


ALTER TABLE public.events OWNER TO telegram_events_db_user;

--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.events_id_seq OWNER TO telegram_events_db_user;

--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: group_messages; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.group_messages (
    id bigint NOT NULL,
    chat_id bigint NOT NULL,
    message_id bigint NOT NULL,
    date_ts timestamp without time zone NOT NULL,
    from_user_id bigint,
    from_user_name text,
    content_type text,
    text text,
    caption text,
    media_group_id text,
    photo_file_id text,
    photo_unique_id text,
    video_file_id text,
    video_unique_id text,
    audio_file_id text,
    audio_unique_id text,
    voice_file_id text,
    voice_unique_id text,
    document_file_id text,
    document_unique_id text,
    extra_json text
);


ALTER TABLE public.group_messages OWNER TO telegram_events_db_user;

--
-- Name: group_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.group_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.group_messages_id_seq OWNER TO telegram_events_db_user;

--
-- Name: group_messages_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.group_messages_id_seq OWNED BY public.group_messages.id;


--
-- Name: reagent_supplies; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.reagent_supplies (
    id integer NOT NULL,
    reagent character varying(128) NOT NULL,
    qty numeric(14,3) NOT NULL,
    unit character varying(16) DEFAULT 'kg'::character varying NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    source character varying(128),
    location character varying(128),
    comment text
);


ALTER TABLE public.reagent_supplies OWNER TO telegram_events_db_user;

--
-- Name: reagent_supplies_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.reagent_supplies_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.reagent_supplies_id_seq OWNER TO telegram_events_db_user;

--
-- Name: reagent_supplies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.reagent_supplies_id_seq OWNED BY public.reagent_supplies.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    username text,
    full_name text,
    first_seen timestamp without time zone DEFAULT now()
);


ALTER TABLE public.users OWNER TO telegram_events_db_user;

--
-- Name: well_channels; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_channels (
    id integer NOT NULL,
    well_id integer NOT NULL,
    channel integer NOT NULL,
    started_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'utc'::text) NOT NULL,
    ended_at timestamp without time zone,
    note character varying(500),
    created_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'utc'::text) NOT NULL,
    updated_at timestamp without time zone DEFAULT (now() AT TIME ZONE 'utc'::text) NOT NULL
);


ALTER TABLE public.well_channels OWNER TO telegram_events_db_user;

--
-- Name: well_channels_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_channels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_channels_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_channels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_channels_id_seq OWNED BY public.well_channels.id;


--
-- Name: well_construction; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_construction (
    id integer NOT NULL,
    well_no text NOT NULL,
    horizon text,
    prod_casing_diam_mm numeric(10,2),
    prod_casing_depth_m numeric(10,2),
    current_bottomhole_m numeric(10,2),
    perf_intervals_m text,
    tubing_diam_mm numeric(10,2),
    tubing_shoe_depth_m numeric(10,2),
    packer_depth_m numeric(10,2),
    adapter_depth_m numeric(10,2),
    pattern_stuck_depth_m numeric(10,2),
    choke_diam_mm numeric(10,2),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    data_as_of date
);


ALTER TABLE public.well_construction OWNER TO telegram_events_db_user;

--
-- Name: well_construction_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_construction_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_construction_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_construction_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_construction_id_seq OWNED BY public.well_construction.id;


--
-- Name: well_equipment; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_equipment (
    id integer NOT NULL,
    well_id integer NOT NULL,
    type_code character varying(50) NOT NULL,
    serial_number character varying(100),
    channel integer,
    installed_at timestamp without time zone NOT NULL,
    removed_at timestamp without time zone,
    note character varying(500),
    created_at timestamp without time zone NOT NULL,
    updated_at timestamp without time zone NOT NULL
);


ALTER TABLE public.well_equipment OWNER TO telegram_events_db_user;

--
-- Name: well_equipment_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_equipment_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_equipment_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_equipment_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_equipment_id_seq OWNED BY public.well_equipment.id;


--
-- Name: well_notes; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_notes (
    id integer NOT NULL,
    well_id integer NOT NULL,
    note_time timestamp without time zone NOT NULL,
    text text NOT NULL,
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now()
);


ALTER TABLE public.well_notes OWNER TO telegram_events_db_user;

--
-- Name: well_notes_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_notes_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_notes_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_notes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_notes_id_seq OWNED BY public.well_notes.id;


--
-- Name: well_perforation_interval; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_perforation_interval (
    id integer NOT NULL,
    well_construction_id integer NOT NULL,
    interval_index integer NOT NULL,
    top_depth_m numeric(10,2),
    bottom_depth_m numeric(10,2)
);


ALTER TABLE public.well_perforation_interval OWNER TO telegram_events_db_user;

--
-- Name: well_perforation_interval_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_perforation_interval_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_perforation_interval_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_perforation_interval_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_perforation_interval_id_seq OWNED BY public.well_perforation_interval.id;


--
-- Name: well_status; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.well_status (
    id integer NOT NULL,
    well_id integer NOT NULL,
    status text NOT NULL,
    dt_start timestamp with time zone DEFAULT now() NOT NULL,
    dt_end timestamp with time zone,
    note text
);


ALTER TABLE public.well_status OWNER TO telegram_events_db_user;

--
-- Name: well_status_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.well_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.well_status_id_seq OWNER TO telegram_events_db_user;

--
-- Name: well_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.well_status_id_seq OWNED BY public.well_status.id;


--
-- Name: wells; Type: TABLE; Schema: public; Owner: telegram_events_db_user
--

CREATE TABLE public.wells (
    id integer NOT NULL,
    number integer NOT NULL,
    name character varying(64),
    lat double precision,
    lon double precision,
    current_status character varying(32),
    description text
);


ALTER TABLE public.wells OWNER TO telegram_events_db_user;

--
-- Name: wells_id_seq; Type: SEQUENCE; Schema: public; Owner: telegram_events_db_user
--

CREATE SEQUENCE public.wells_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.wells_id_seq OWNER TO telegram_events_db_user;

--
-- Name: wells_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: telegram_events_db_user
--

ALTER SEQUENCE public.wells_id_seq OWNED BY public.wells.id;


--
-- Name: dashboard_login_log id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.dashboard_login_log ALTER COLUMN id SET DEFAULT nextval('public.dashboard_login_log_id_seq'::regclass);


--
-- Name: dashboard_users id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.dashboard_users ALTER COLUMN id SET DEFAULT nextval('public.dashboard_users_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: group_messages id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.group_messages ALTER COLUMN id SET DEFAULT nextval('public.group_messages_id_seq'::regclass);


--
-- Name: reagent_supplies id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.reagent_supplies ALTER COLUMN id SET DEFAULT nextval('public.reagent_supplies_id_seq'::regclass);


--
-- Name: well_channels id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_channels ALTER COLUMN id SET DEFAULT nextval('public.well_channels_id_seq'::regclass);


--
-- Name: well_construction id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_construction ALTER COLUMN id SET DEFAULT nextval('public.well_construction_id_seq'::regclass);


--
-- Name: well_equipment id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_equipment ALTER COLUMN id SET DEFAULT nextval('public.well_equipment_id_seq'::regclass);


--
-- Name: well_notes id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_notes ALTER COLUMN id SET DEFAULT nextval('public.well_notes_id_seq'::regclass);


--
-- Name: well_perforation_interval id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_perforation_interval ALTER COLUMN id SET DEFAULT nextval('public.well_perforation_interval_id_seq'::regclass);


--
-- Name: well_status id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_status ALTER COLUMN id SET DEFAULT nextval('public.well_status_id_seq'::regclass);


--
-- Name: wells id; Type: DEFAULT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.wells ALTER COLUMN id SET DEFAULT nextval('public.wells_id_seq'::regclass);


--
-- Data for Name: alembic_version; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.alembic_version (version_num) FROM stdin;
c3e8c8ab68aa
\.


--
-- Data for Name: chats; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.chats (id, title, is_forum) FROM stdin;
\.


--
-- Data for Name: dashboard_login_log; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.dashboard_login_log (id, user_id, login_at, logout_at, ip_address, user_agent) FROM stdin;
2	6	2025-12-05 00:12:49.486127+00	2025-12-05 00:14:09.330509+00	104.28.131.166	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
3	6	2025-12-05 05:08:48.78+00	\N	104.28.51.237	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
5	8	2025-12-05 09:35:08.335736+00	\N	213.230.93.57	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0
6	8	2025-12-05 09:45:54.130496+00	\N	213.230.93.57	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36
4	6	2025-12-05 07:47:04.745263+00	2025-12-05 11:05:09.933263+00	172.225.105.7	Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1
1	6	2025-12-04 23:39:05.165201+00	2025-12-06 13:16:53.914494+00	127.0.0.1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
7	6	2025-12-06 13:16:54.093857+00	2025-12-06 13:19:53.48739+00	127.0.0.1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
8	6	2025-12-06 13:19:53.689996+00	2025-12-08 07:50:54.817101+00	127.0.0.1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
9	6	2025-12-08 07:50:55.007103+00	\N	127.0.0.1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15
\.


--
-- Data for Name: dashboard_users; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.dashboard_users (id, username, password_hash, is_admin, is_active, created_at, last_login_at, email, first_name, last_name, login_count, can_view_reagents) FROM stdin;
4	user_4	5994471abb01112afcc18159f6cc74b4f511b99806da59b3caf5a9c173cacfc5	f	t	2025-12-03 22:19:59.67486	\N	sssddfff@cc.dd	sss	edfefc	0	f
8	skarakaev	3f8078c73215cb7bbb631da12dd8539b05fd42adffee3e2dcd7ae86bbed7d70c	f	t	2025-12-05 09:35:03.730481	2025-12-05 09:45:54.120343	skarakaev@gmail.com	Серик	Каракаев	0	f
6	admin	240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9	t	t	2025-12-04 11:40:30.283961	2025-12-08 07:50:54.576835	admin@example.com	Admin	User	0	f
\.


--
-- Data for Name: events; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.events (id, chat_id, user_id, well, event_type, reagent, qty, p_tube, p_line, event_time, description, lat, lon, created_at, equip_type, equip_points, equip_other, purge_phase, other_kind, geo_status) FROM stdin;
4	-4833184749	6730772526	43	reagent	SW-OF	1	16.96	16.79	2025-11-05 11:17:59.141745	\N	\N	\N	2025-11-05 06:18:23.48646	\N	\N	\N	\N	\N	skipped_by_user
15	-4833184749	6730772526	43	reagent	Oil Foam	1	16.96	16.61	2025-11-05 18:33:57.023865	\N	44.049173	58.702639	2025-11-05 13:34:09.075644	\N	\N	\N	\N	\N	received
5	-4833184749	0	107	equip	\N	\N	\N	\N	2025-10-18 15:20:00	Снято со скважины 117	\N	\N	2025-11-05 08:29:37.766773	gate	\N	\N	\N	\N	skipped_by_user
8	-4833184749	0	89	equip	\N	\N	\N	\N	2025-10-04 15:00:00	Снято со скважины 85	\N	\N	2025-11-05 08:55:36.787485	gate	\N	\N	\N	\N	\N
9	-4833184749	0	107	equip	\N	\N	\N	\N	2025-10-02 15:00:00	Снято со скважины 85	\N	\N	2025-11-05 08:55:36.787485	smod	["wellhead"]	\N	\N	\N	\N
1	-4950409379	6730772526	89	reagent	BT-10	1	17.56	16.96	2025-11-04 22:07:46.640532		\N	\N	2025-11-04 22:41:01.417998	\N	\N	\N	\N	\N	skipped_by_user
16	-4833184749	1042194608	43	other	\N	\N	\N	\N	2025-11-05 22:03:16.45536	24.2 16.9	44.049779	58.699374	2025-11-05 17:05:18.511804	\N	\N	\N	\N	\N	received
18	-4833184749	6730772526	43	other	\N	\N	\N	\N	2025-11-06 02:04:55.161113	[Давления ДО: Труб.=43.2 атм; Лин.=16.9 атм | ПОСЛЕ: Труб.=17.7 атм; Лин.=16.6 атм]	\N	\N	2025-11-05 21:06:11.59256	\N	\N	\N	\N	\N	skipped_by_user
29	-4833184749	6730772526	48	equip	\N	\N	\N	\N	2025-11-06 17:50:18.707873	Снято со скважины 140	\N	\N	2025-11-06 12:50:26.874948	smod	["line", "wellhead"]	\N	\N	\N	skipped_by_user
3	-4833184749	6730772526	89	reagent	BT-10	1	17.72	17.24	2025-11-05 09:34:30.295777	\N	\N	\N	2025-11-05 06:08:20.072168	\N	\N	\N	\N	\N	skipped_by_user
10	-4833184749	6730772526	89	reagent	BT-10	1	17.5	16.86	2025-11-05 15:45:20.009266	\N	44.019078	58.722877	2025-11-05 10:46:00.205444	\N	\N	\N	\N	\N	received
11	-4833184749	6730772526	89	pressure	\N	\N	16.87	16.69	2025-11-05 15:55:35.169596	\N	44.019042	58.722795	2025-11-05 10:55:53.868923	\N	\N	\N	\N	\N	received
12	-4833184749	6730772526	43	pressure	\N	\N	16.85	16.34	2025-11-05 16:21:50.182421	\N	44.050186	58.699747	2025-11-05 11:22:10.613604	\N	\N	\N	\N	\N	received
13	-4833184749	6730772526	107	pressure	\N	\N	17.61	16.69	2025-11-05 16:37:26.13786	\N	44.048815	58.681296	2025-11-05 11:37:37.713989	\N	\N	\N	\N	\N	received
14	-4833184749	6730772526	61	pressure	\N	\N	18.16	17.17	2025-11-05 16:50:32.048171	\N	44.07594	58.663904	2025-11-05 11:50:54.903153	\N	\N	\N	\N	\N	received
17	-4833184749	1042194608	89	reagent	BT-10	1	17.54	17.01	2025-11-05 22:38:18.024973	\N	44.01899	58.722613	2025-11-05 17:40:04.488888	\N	\N	\N	\N	\N	received
19	-4833184749	7392840491	89	reagent	BT-10	1	18.9	18.35	2025-11-06 08:57:07.718877	\N	44.019106	58.722816	2025-11-06 03:57:42.834411	\N	\N	\N	\N	\N	received
20	-4833184749	7392840491	43	reagent	SW-OF	1	18.43	17.84	2025-11-06 10:00:57.540461	\N	44.050174	58.699719	2025-11-06 05:02:02.392001	\N	\N	\N	\N	\N	received
23	-4833184749	7392840491	89	reagent	BT-10	1	18.31	17.75	2025-11-06 14:07:39.568495	\N	44.019177	58.722794	2025-11-06 09:08:57.856881	\N	\N	\N	\N	\N	received
24	-4833184749	7392840491	43	reagent	Liquid Foam	1	17.66	17.22	2025-11-06 14:27:10.657824	\N	\N	\N	2025-11-06 09:27:37.916194	\N	\N	\N	\N	\N	timeout
28	-4833184749	7392840491	43	reagent	SW-OF	1	17.63	17.19	2025-11-06 16:17:15.934694	\N	\N	\N	2025-11-06 11:17:43.965093	\N	\N	\N	\N	\N	timeout
21	-4833184749	6730772526	43	pressure	\N	\N	17.93	17.48	2025-11-06 12:33:44.112118	\N	\N	\N	2025-11-06 07:33:54.040135	\N	\N	\N	\N	\N	skipped_by_user
22	-4833184749	6730772526	107	pressure	\N	\N	18.65	17.65	2025-11-06 12:49:51.80424	\N	\N	\N	2025-11-06 07:50:03.230622	\N	\N	\N	\N	\N	skipped_by_user
25	-4833184749	6730772526	140	pressure	\N	\N	18.27	17.19	2025-11-06 14:38:20.58949	\N	\N	\N	2025-11-06 09:38:34.51273	\N	\N	\N	\N	\N	skipped_by_user
26	-4833184749	7392840491	107	pressure	\N	\N	18.32	17.4	2025-11-06 14:46:50.992323	\N	44.048819	58.681368	2025-11-06 09:47:57.760183	\N	\N	\N	\N	\N	received
27	-4833184749	7392840491	61	pressure	\N	\N	19.06	17.78	2025-11-06 15:02:56.27293	\N	44.075968	58.663843	2025-11-06 10:03:20.975134	\N	\N	\N	\N	\N	received
30	-4833184749	7392840491	89	reagent	BT-10	1	17.24	16.65	2025-11-06 22:05:56.724175	Вброс	44.019106	58.722816	2025-11-06 17:06:21.160973	\N	\N	\N	\N	\N	received
31	-4833184749	7392840491	43	reagent	SW-OF	1	16.9	16.45	2025-11-06 22:22:26.109798	Вброс	44.050169	58.699647	2025-11-06 17:22:47.345066	\N	\N	\N	\N	\N	received
32	-4833184749	7392840491	89	pressure	\N	\N	18.54	17.09	2025-11-06 23:04:15.068578		44.037752	58.686247	2025-11-06 18:04:43.90591	\N	\N	\N	\N	\N	received
33	-4833184749	7392840491	43	pressure	\N	\N	16.9	16.58	2025-11-06 23:08:15.574385		44.050169	58.699647	2025-11-06 18:08:34.987383	\N	\N	\N	\N	\N	received
34	-4833184749	7392840491	107	pressure	\N	\N	17.91	16.77	2025-11-06 23:09:52.618475		44.050169	58.699647	2025-11-06 18:10:13.297128	\N	\N	\N	\N	\N	received
35	-4833184749	7392840491	61	pressure	\N	\N	18.27	17.08	2025-11-06 23:12:02.244129		\N	\N	2025-11-06 18:12:22.869806	\N	\N	\N	\N	\N	timeout
36	-4833184749	7392840491	48	pressure	\N	\N	16.85	16.56	2025-11-06 23:19:44.531896		44.050169	58.699647	2025-11-06 18:20:05.169119	\N	\N	\N	\N	\N	received
37	-4833184749	7392840491	89	reagent	BT-10	1	17.42	17.04	2025-11-07 09:32:21.97728	Вброс	44.019224	58.722716	2025-11-07 04:32:43.615022	\N	\N	\N	\N	\N	received
38	-4833184749	7392840491	43	reagent	SW-OF	1	17.18	16.75	2025-11-07 09:48:49.130682	Вброс	44.05011	58.699698	2025-11-07 04:49:06.090836	\N	\N	\N	\N	\N	received
39	-4833184749	7392840491	48	pressure	\N	\N	17.81	16.77	2025-11-07 10:07:23.793588		44.058514	58.68674	2025-11-07 05:07:40.101992	\N	\N	\N	\N	\N	received
40	-4833184749	7392840491	107	pressure	\N	\N	18.1	16.85	2025-11-07 10:16:03.797687		44.049585	58.680267	2025-11-07 05:16:19.589043	\N	\N	\N	\N	\N	received
41	-4833184749	7392840491	61	pressure	\N	\N	18.05	17.25	2025-11-07 10:29:40.546347		44.075948	58.66386	2025-11-07 05:29:59.247202	\N	\N	\N	\N	\N	received
42	-4833184749	7392840491	89	pressure	\N	\N	17.08	16.54	2025-11-07 17:29:49.057127		44.019157	58.722811	2025-11-07 12:30:02.438933	\N	\N	\N	\N	\N	received
43	-4833184749	7392840491	43	pressure	\N	\N	16.82	16.36	2025-11-07 17:53:04.94879		44.050206	58.699918	2025-11-07 12:53:20.106057	\N	\N	\N	\N	\N	received
49	-4833184749	7392840491	48	pressure	\N	\N	16.93	16.34	2025-11-07 18:01:35.756386		44.058443	58.686762	2025-11-07 13:01:50.205602	\N	\N	\N	\N	\N	received
50	-4833184749	7392840491	107	other	\N	\N	\N	\N	2025-11-06 13:44:00	[Давления ДО: Труб.=17.83 атм; Лин.=16.78 атм | ПОСЛЕ: Труб.=17.82 атм; Лин.=16.72 атм]	\N	\N	2025-11-07 13:02:53.023526	\N	\N	\N	\N	\N	\N
51	-4833184749	7392840491	107	pressure	\N	\N	17.57	16.54	2025-11-07 18:08:30.564246		44.048815	58.681296	2025-11-07 13:08:49.376092	\N	\N	\N	\N	\N	received
52	-4833184749	7392840491	107	reagent	1259	1	17.18	16.4	2025-11-07 18:21:16.257514	Вброс	44.048739	58.681247	2025-11-07 13:21:33.250528	\N	\N	\N	\N	\N	received
53	-4833184749	6730772526	61	pressure	\N	\N	18.81	17.02	2025-11-07 18:30:50.597279		\N	\N	2025-11-07 13:31:00.693455	\N	\N	\N	\N	\N	skipped_by_user
54	-4833184749	484694023	107	other	\N	\N	\N	\N	2025-11-08 00:03:23		\N	\N	2025-11-07 19:03:31.07263	\N	\N	\N	\N	\N	skipped_by_user
55	-4833184749	6730772526	89	pressure	\N	\N	17.64	16.74	2025-11-08 00:20:01		\N	\N	2025-11-07 19:20:09.743744	\N	\N	\N	\N	\N	skipped_by_user
56	-4833184749	6730772526	43	reagent	SW-OF	1	16.75	16.32	2025-11-07 21:53:10		\N	\N	2025-11-07 19:24:37.664947	\N	\N	\N	\N	\N	skipped_by_user
58	-4833184749	6730772526	107	pressure	\N	\N	17.36	16.47	2025-11-08 00:31:37		\N	\N	2025-11-07 19:31:49.641824	\N	\N	\N	\N	\N	skipped_by_user
59	-4833184749	7392840491	89	reagent	BT-10	1	16.75	16.35	2025-11-08 09:22:27	Вброс	44.019145	58.722783	2025-11-08 04:22:42.329258	\N	\N	\N	\N	\N	received
60	-4833184749	7392840491	43	reagent	SW-OF	1	16.4	15.82	2025-11-08 09:37:05	Вброс	44.050265	58.69968	2025-11-08 04:37:26.878286	\N	\N	\N	\N	\N	received
61	-4833184749	7392840491	107	pressure	\N	\N	17.69	16.55	2025-11-08 09:45:25		44.048715	58.681192	2025-11-08 04:45:39.408101	\N	\N	\N	\N	\N	received
64	-4833184749	7392840491	87	pressure	\N	\N	22.99	22.58	2025-11-08 10:14:07		44.059839	58.696407	2025-11-08 05:14:21.09592	\N	\N	\N	\N	\N	received
65	-4833184749	7392840491	48	pressure	\N	\N	17.9	16.14	2025-11-08 10:30:01		44.058463	58.686746	2025-11-08 05:30:16.800498	\N	\N	\N	\N	\N	received
66	-4833184749	7392840491	61	pressure	\N	\N	17.95	16.79	2025-11-08 10:44:41		44.075968	58.663843	2025-11-08 05:44:56.115048	\N	\N	\N	\N	\N	received
111	-4833184749	6238913206	89	reagent	1259	1	17.04	16.8	2025-11-10 20:18:53.246112		44.019066	58.72285	2025-11-10 15:19:26.768893	\N	\N	\N	\N	\N	received
63	-4833184749	7392840491	87	equip	\N	\N	\N	\N	2025-11-08 10:12:39	Установка манометра	44.059974	58.696406	2025-11-08 05:13:20.158685	smod	["line", "wellhead"]	\N	\N	\N	received
68	-4833184749	6730772526	43	pressure	\N	\N	17.19	16.71	2025-11-08 17:19:08.456945		\N	\N	2025-11-08 12:19:16.383132	\N	\N	\N	\N	\N	skipped_by_user
69	-4833184749	6730772526	48	pressure	\N	\N	17.81	16.56	2025-11-08 17:20:38.213103		\N	\N	2025-11-08 12:20:47.481098	\N	\N	\N	\N	\N	skipped_by_user
70	-4833184749	6730772526	87	pressure	\N	\N	18.66	18	2025-11-08 17:21:26.354694		\N	\N	2025-11-08 12:21:34.967346	\N	\N	\N	\N	\N	skipped_by_user
71	-4833184749	6730772526	61	pressure	\N	\N	17.78	16.84	2025-11-08 17:22:18.953246		\N	\N	2025-11-08 12:22:26.895544	\N	\N	\N	\N	\N	skipped_by_user
72	-4833184749	6730772526	89	pressure	\N	\N	17.21	16.73	2025-11-08 17:22:58.139242		\N	\N	2025-11-08 12:23:05.664964	\N	\N	\N	\N	\N	skipped_by_user
73	-4833184749	7392840491	89	reagent	BT-10	1	17.1	16.72	2025-11-08 19:10:58.719031		44.019193	58.722705	2025-11-08 14:11:13.284466	\N	\N	\N	\N	\N	received
74	-4833184749	7392840491	43	reagent	SW-OF	1	17.09	16.45	2025-11-08 19:27:39.523044		44.050126	58.699797	2025-11-08 14:27:54.007263	\N	\N	\N	\N	\N	received
75	-4833184749	7392840491	87	pressure	\N	\N	18.93	18.21	2025-11-08 19:35:56.986909		44.059855	58.696506	2025-11-08 14:36:12.624116	\N	\N	\N	\N	\N	received
76	-4833184749	7392840491	48	pressure	\N	\N	17.54	16.47	2025-11-08 19:40:23.95854		44.058463	58.686746	2025-11-08 14:40:38.560217	\N	\N	\N	\N	\N	received
77	-4833184749	7392840491	61	pressure	\N	\N	17.64	16.76	2025-11-08 19:56:55.301839		44.075928	58.663877	2025-11-08 14:57:16.429852	\N	\N	\N	\N	\N	received
78	-4833184749	7392840491	89	reagent	BT-10	1	16.8	16.57	2025-11-09 09:10:12.244758		44.01911	58.722888	2025-11-09 04:10:25.47138	\N	\N	\N	\N	\N	received
79	-4833184749	7392840491	43	reagent	SW-OF	1	16.96	16.4	2025-11-09 09:26:02.314711		44.05011	58.699698	2025-11-09 04:26:16.997637	\N	\N	\N	\N	\N	received
80	-4833184749	7392840491	87	pressure	\N	\N	22.45	21.91	2025-11-09 09:33:39.623023		44.059811	58.696468	2025-11-09 04:33:54.462935	\N	\N	\N	\N	\N	received
81	-4833184749	7392840491	48	pressure	\N	\N	16.76	16.4	2025-11-09 09:38:22.054856		44.058451	58.686718	2025-11-09 04:38:38.137529	\N	\N	\N	\N	\N	received
82	-4833184749	7392840491	61	pressure	\N	\N	17.37	16.73	2025-11-09 09:50:20.901731		44.075924	58.663805	2025-11-09 04:50:33.148387	\N	\N	\N	\N	\N	received
83	-4833184749	6730772526	61	pressure	\N	\N	18.21	17.22	2025-11-09 11:37:49.197518		\N	\N	2025-11-09 06:37:56.978095	\N	\N	\N	\N	\N	skipped_by_user
84	-4833184749	6730772526	43	pressure	\N	\N	17.32	16.77	2025-11-09 11:38:17.3834		\N	\N	2025-11-09 06:38:24.736689	\N	\N	\N	\N	\N	skipped_by_user
85	-4833184749	6730772526	48	pressure	\N	\N	18.43	16.81	2025-11-09 11:38:44.296244		\N	\N	2025-11-09 06:38:50.664618	\N	\N	\N	\N	\N	skipped_by_user
86	-4833184749	6730772526	87	pressure	\N	\N	22.81	22.32	2025-11-09 11:39:10.332444		\N	\N	2025-11-09 06:39:16.914561	\N	\N	\N	\N	\N	skipped_by_user
87	-4833184749	6730772526	89	pressure	\N	\N	17.96	17.15	2025-11-09 11:39:37.242899		\N	\N	2025-11-09 06:39:43.701352	\N	\N	\N	\N	\N	skipped_by_user
88	-4833184749	7392840491	89	pressure	\N	\N	17.34	17.06	2025-11-09 18:33:07.156356		44.037752	58.686247	2025-11-09 13:33:20.710125	\N	\N	\N	\N	\N	received
89	-4833184749	7392840491	43	pressure	\N	\N	17.96	16.96	2025-11-09 18:35:43.737533		44.075924	58.663805	2025-11-09 13:35:59.863784	\N	\N	\N	\N	\N	received
90	-4833184749	7392840491	87	pressure	\N	\N	22.61	22.07	2025-11-09 18:37:28.366876		44.075924	58.663805	2025-11-09 13:37:48.684619	\N	\N	\N	\N	\N	received
91	-4833184749	7392840491	48	pressure	\N	\N	17.85	16.84	2025-11-09 18:39:12.200754		44.037752	58.686247	2025-11-09 13:39:28.82373	\N	\N	\N	\N	\N	received
92	-4833184749	7392840491	61	pressure	\N	\N	19.06	17.47	2025-11-09 18:40:06.609221		44.075924	58.663805	2025-11-09 13:40:22.761705	\N	\N	\N	\N	\N	received
93	-4833184749	6730772526	48	other	\N	\N	\N	\N	2025-11-09 19:19:05.209604		\N	\N	2025-11-09 14:19:22.014568	\N	\N	\N	\N	\N	skipped_by_user
94	-4833184749	7392840491	89	reagent	BT-10	1	17.17	16.95	2025-11-09 19:43:51.773603		44.019054	58.722822	2025-11-09 14:44:09.921895	\N	\N	\N	\N	\N	received
95	-4833184749	7392840491	43	reagent	SW-OF	1	17.49	16.83	2025-11-09 19:58:58.780373		44.050265	58.69968	2025-11-09 14:59:12.96741	\N	\N	\N	\N	\N	received
96	-4833184749	1042194608	89	pressure	\N	\N	17.86	17.01	2025-11-09 23:33:27.635174		\N	\N	2025-11-09 18:34:00.703433	\N	\N	\N	\N	\N	skipped_by_user
97	-4833184749	1042194608	87	pressure	\N	\N	22.67	22.07	2025-11-09 23:36:23.691051		\N	\N	2025-11-09 18:36:39.112809	\N	\N	\N	\N	\N	skipped_by_user
98	-4833184749	1042194608	48	pressure	\N	\N	17.2	16.54	2025-11-09 23:40:02.224916		\N	\N	2025-11-09 18:40:12.839009	\N	\N	\N	\N	\N	skipped_by_user
99	-4833184749	1042194608	43	pressure	\N	\N	17.43	16.58	2025-11-09 23:40:54.734499		\N	\N	2025-11-09 18:41:05.014816	\N	\N	\N	\N	\N	skipped_by_user
100	-4833184749	1042194608	61	pressure	\N	\N	18.73	17.17	2025-11-09 23:41:36.67147		\N	\N	2025-11-09 18:41:48.01007	\N	\N	\N	\N	\N	skipped_by_user
101	-4833184749	7392840491	89	reagent	BT-10	1	17.04	16.79	2025-11-10 08:26:06.020537		44.019197	58.722777	2025-11-10 03:26:18.997	\N	\N	\N	\N	\N	received
102	-4833184749	7392840491	43	reagent	SW-OF	1	17.59	16.6	2025-11-10 08:42:25.270058		44.050122	58.699725	2025-11-10 03:42:39.158819	\N	\N	\N	\N	\N	received
103	-4833184749	1042194608	87	pressure	\N	\N	21.96	21.31	2025-11-10 08:59:15.426334		\N	\N	2025-11-10 03:59:28.259394	\N	\N	\N	\N	\N	skipped_by_user
104	-4833184749	1042194608	48	pressure	\N	\N	16.81	16.47	2025-11-10 09:00:20.041313		\N	\N	2025-11-10 04:00:31.411516	\N	\N	\N	\N	\N	skipped_by_user
105	-4833184749	1042194608	61	pressure	\N	\N	18.6	17.15	2025-11-10 09:01:06.811914		\N	\N	2025-11-10 04:01:19.954221	\N	\N	\N	\N	\N	skipped_by_user
106	-4833184749	6238913206	89	pressure	\N	\N	18.29	17.47	2025-11-10 13:36:13.638652		44.019137	58.722827	2025-11-10 08:37:11.034547	\N	\N	\N	\N	\N	received
107	-4833184749	1042194608	43	pressure	\N	\N	18.03	17.07	2025-11-10 13:53:51.302257		44.050181	58.699675	2025-11-10 08:54:02.076011	\N	\N	\N	\N	\N	received
108	-4833184749	6740275295	87	pressure	\N	\N	22.66	22.1	2025-11-10 14:14:36.140353		44.059866	58.696346	2025-11-10 09:14:59.952288	\N	\N	\N	\N	\N	received
109	-4833184749	1042194608	48	pressure	\N	\N	18.2	17.01	2025-11-10 14:21:37.344244		44.058463	58.686746	2025-11-10 09:21:51.114211	\N	\N	\N	\N	\N	received
110	-4833184749	6740275295	61	pressure	\N	\N	18.62	17.41	2025-11-10 14:37:41.539004		44.07584	58.6638	2025-11-10 09:38:00.330875	\N	\N	\N	\N	\N	received
112	-4833184749	1042194608	43	reagent	SW-OF	1	17.61	16.6	2025-11-10 20:37:29.511971		44.050252	58.699464	2025-11-10 15:37:43.065421	\N	\N	\N	\N	\N	received
113	-4833184749	1042194608	87	pressure	\N	\N	21.9	21.23	2025-11-10 20:55:37.472623		\N	\N	2025-11-10 15:55:47.692287	\N	\N	\N	\N	\N	skipped_by_user
114	-4833184749	1042194608	48	pressure	\N	\N	17.31	16.52	2025-11-10 20:56:30.257364		\N	\N	2025-11-10 15:56:47.214736	\N	\N	\N	\N	\N	skipped_by_user
115	-4833184749	1042194608	61	pressure	\N	\N	18.05	16.94	2025-11-10 20:57:20.263524		\N	\N	2025-11-10 15:57:34.62048	\N	\N	\N	\N	\N	skipped_by_user
116	-4833184749	6740275295	89	reagent	1259	1	17.76	17.07	2025-11-11 09:04:11.502778		44.019118	58.722844	2025-11-11 04:04:29.301824	\N	\N	\N	\N	\N	received
117	-4833184749	6238913206	43	reagent	SW-OF	1	17.66	16.68	2025-11-11 09:21:45.924013		\N	\N	2025-11-11 04:23:17.522368	\N	\N	\N	\N	\N	timeout
118	-4833184749	6740275295	87	pressure	\N	\N	21.28	20.65	2025-11-11 09:35:08.708828		44.059907	58.6965	2025-11-11 04:35:34.576143	\N	\N	\N	\N	\N	received
119	-4833184749	6740275295	48	pressure	\N	\N	17.24	16.55	2025-11-11 09:40:31.588968		44.058383	58.686813	2025-11-11 04:40:49.027233	\N	\N	\N	\N	\N	received
120	-4833184749	1042194608	61	reagent	Oil Foam	1	17.76	17.06	2025-11-11 10:00:06.365671		44.07588	58.663767	2025-11-11 05:00:57.94592	\N	\N	\N	\N	\N	received
121	-4833184749	6238913206	89	pressure	\N	\N	17.53	16.87	2025-11-11 14:42:04.412097		\N	\N	2025-11-11 09:42:35.097281	\N	\N	\N	\N	\N	skipped_by_user
122	-4833184749	6238913206	87	pressure	\N	\N	22.84	22.4	2025-11-11 14:43:25.908578		\N	\N	2025-11-11 09:43:41.260649	\N	\N	\N	\N	\N	skipped_by_user
123	-4833184749	6238913206	48	pressure	\N	\N	16.97	16.52	2025-11-11 14:44:27.563376		\N	\N	2025-11-11 09:44:42.096265	\N	\N	\N	\N	\N	skipped_by_user
124	-4833184749	6238913206	43	pressure	\N	\N	17.55	16.56	2025-11-11 14:45:19.649309		\N	\N	2025-11-11 09:45:31.460526	\N	\N	\N	\N	\N	skipped_by_user
125	-4833184749	6238913206	61	pressure	\N	\N	18.84	17.14	2025-11-11 14:46:11.93285		\N	\N	2025-11-11 09:46:23.774831	\N	\N	\N	\N	\N	skipped_by_user
126	-4833184749	6238913206	89	reagent	1259	1	17.4	16.73	2025-11-11 20:40:03.54443		44.019165	58.722766	2025-11-11 15:40:20.463083	\N	\N	\N	\N	\N	received
127	-4833184749	6238913206	43	reagent	SW-OF	1	17.23	16.2	2025-11-11 20:55:35.308148		44.050138	58.699637	2025-11-11 15:55:55.859551	\N	\N	\N	\N	\N	received
128	-4833184749	1042194608	87	pressure	\N	\N	22.36	21.68	2025-11-11 21:08:49.86092		\N	\N	2025-11-11 16:09:00.241342	\N	\N	\N	\N	\N	skipped_by_user
129	-4833184749	1042194608	48	pressure	\N	\N	17.82	16.11	2025-11-11 21:09:35.761891		\N	\N	2025-11-11 16:09:45.158475	\N	\N	\N	\N	\N	skipped_by_user
130	-4833184749	1042194608	61	pressure	\N	\N	18.32	16.6	2025-11-11 21:10:34.580039		\N	\N	2025-11-11 16:10:46.956187	\N	\N	\N	\N	\N	skipped_by_user
131	-4833184749	6740275295	89	reagent	1259	1	17.72	17.06	2025-11-12 09:16:52.582709		44.019137	58.722827	2025-11-12 04:17:13.180958	\N	\N	\N	\N	\N	received
132	-4833184749	1042194608	43	reagent	SW-OF	1	18.02	16.55	2025-11-12 09:34:02.872841		44.05015	58.699664	2025-11-12 04:34:13.889871	\N	\N	\N	\N	\N	received
133	-4833184749	1042194608	87	pressure	\N	\N	22.24	21.63	2025-11-12 09:41:42.238805		44.059795	58.696368	2025-11-12 04:41:53.574065	\N	\N	\N	\N	\N	received
134	-4833184749	6238913206	48	pressure	\N	\N	17.04	16.46	2025-11-12 09:53:19.578874		44.058435	58.686807	2025-11-12 04:53:35.081397	\N	\N	\N	\N	\N	received
135	-4833184749	1042194608	61	pressure	\N	\N	18.54	16.98	2025-11-12 10:33:53.855919		\N	\N	2025-11-12 05:34:05.586183	\N	\N	\N	\N	\N	skipped_by_user
136	-4833184749	6238913206	48	equip	\N	\N	\N	\N	2025-11-12 17:33:47.334924	Сняли со скв 107	44.058443	58.686762	2025-11-12 12:34:40.392817	gate	{}	\N	\N	\N	received
137	-4833184749	6238913206	48	purge	\N	\N	16.56	16.06	2025-11-12 17:37:46.094263		44.058435	58.686807	2025-11-12 12:40:14.007333	\N	\N	\N	start	\N	received
138	-4833184749	6238913206	48	purge	\N	\N	1.7	16.17	2025-11-12 17:58:24.702706		44.058455	58.68679	2025-11-12 12:59:04.461658	\N	\N	\N	press	\N	received
139	-4833184749	6238913206	48	purge	\N	\N	23.15	16.18	2025-11-12 18:05:01.163089		44.058463	58.686746	2025-11-12 13:05:29.914901	\N	\N	\N	stop	\N	received
140	-4833184749	1042194608	89	reagent	1259	1	17.78	17.24	2025-11-12 21:24:27.630935		44.019101	58.722745	2025-11-12 16:24:43.67934	\N	\N	\N	\N	\N	received
141	-4833184749	1042194608	43	reagent	SW-OF	1	18.29	16.85	2025-11-12 21:43:05.552368		44.05023	58.699785	2025-11-12 16:43:16.04879	\N	\N	\N	\N	\N	received
142	-4833184749	6238913206	61	pressure	\N	\N	18.84	17.4	2025-11-12 21:54:46.024263		\N	\N	2025-11-12 16:55:04.405093	\N	\N	\N	\N	\N	skipped_by_user
143	-4833184749	1042194608	48	reagent	1251	1	17.48	16.78	2025-11-12 22:00:28.835276		44.058451	58.686718	2025-11-12 17:00:40.135677	\N	\N	\N	\N	\N	received
144	-4833184749	6740275295	87	pressure	\N	\N	22.38	21.82	2025-11-12 22:16:45.002276		\N	\N	2025-11-12 17:17:01.916601	\N	\N	\N	\N	\N	skipped_by_user
145	-4833184749	6740275295	89	reagent	1259	1	17.21	16.64	2025-11-13 09:22:04.040661		44.019101	58.722745	2025-11-13 04:22:30.979521	\N	\N	\N	\N	\N	received
146	-4833184749	6740275295	43	reagent	SW-OF	1	17.72	16.24	2025-11-13 09:39:20.140864		44.050209	58.699614	2025-11-13 04:39:32.471081	\N	\N	\N	\N	\N	received
147	-4833184749	6238913206	48	reagent	Super Foam	1	17.57	16.24	2025-11-13 09:49:45.047554		44.058482	58.686729	2025-11-13 04:49:59.409671	\N	\N	\N	\N	\N	received
148	-4833184749	1042194608	61	pressure	\N	\N	18.42	16.7	2025-11-13 10:28:03.178486		\N	\N	2025-11-13 05:28:13.974557	\N	\N	\N	\N	\N	skipped_by_user
149	-4833184749	1042194608	87	pressure	\N	\N	21.52	20.95	2025-11-13 10:28:43.135404		\N	\N	2025-11-13 05:28:53.274242	\N	\N	\N	\N	\N	skipped_by_user
150	-4833184749	6238913206	61	pressure	\N	\N	18.28	16.88	2025-11-13 15:43:02.486962		\N	\N	2025-11-13 10:43:13.942433	\N	\N	\N	\N	\N	skipped_by_user
151	-4833184749	6238913206	43	pressure	\N	\N	17.78	16.27	2025-11-13 15:43:53.3566		\N	\N	2025-11-13 10:44:05.145418	\N	\N	\N	\N	\N	skipped_by_user
152	-4833184749	6238913206	48	pressure	\N	\N	17.71	16.4	2025-11-13 15:45:52.423734		\N	\N	2025-11-13 10:46:03.698336	\N	\N	\N	\N	\N	skipped_by_user
153	-4833184749	6238913206	87	pressure	\N	\N	22.37	21.86	2025-11-13 15:46:39.866162		\N	\N	2025-11-13 10:46:50.374147	\N	\N	\N	\N	\N	skipped_by_user
154	-4833184749	6238913206	89	pressure	\N	\N	16.91	16.4	2025-11-13 15:47:48.760699		\N	\N	2025-11-13 10:47:59.898877	\N	\N	\N	\N	\N	skipped_by_user
155	-4833184749	6740275295	89	reagent	1259	1	16.86	16.21	2025-11-13 21:10:50.399492		44.01907	58.722734	2025-11-13 16:11:10.716194	\N	\N	\N	\N	\N	received
156	-4833184749	6740275295	43	reagent	SW-OF	1	17.33	15.83	2025-11-13 21:29:00.417434		44.050186	58.699747	2025-11-13 16:29:16.128964	\N	\N	\N	\N	\N	received
157	-4833184749	6238913206	48	reagent	1253	1	17.68	16.22	2025-11-13 21:38:34.74278		\N	\N	2025-11-13 16:38:53.646694	\N	\N	\N	\N	\N	skipped_by_user
158	-4833184749	1042194608	87	pressure	\N	\N	21.49	21.03	2025-11-13 22:02:07.797248		\N	\N	2025-11-13 17:02:16.711441	\N	\N	\N	\N	\N	skipped_by_user
159	-4833184749	1042194608	61	pressure	\N	\N	18.36	16.96	2025-11-13 22:03:24.761686		\N	\N	2025-11-13 17:03:42.045894	\N	\N	\N	\N	\N	skipped_by_user
160	-4833184749	6238913206	89	reagent	1259	1	17.37	16.73	2025-11-14 09:25:23.636659		44.019209	58.722805	2025-11-14 04:25:36.811435	\N	\N	\N	\N	\N	received
161	-4833184749	6740275295	43	reagent	SW-OF	1	17.2	16.54	2025-11-14 09:43:57.735038		44.049091	58.700032	2025-11-14 04:44:48.974231	\N	\N	\N	\N	\N	received
162	-4833184749	6740275295	87	pressure	\N	\N	20.63	20.18	2025-11-14 09:53:48.559813		44.059918	58.69634	2025-11-14 04:54:01.666039	\N	\N	\N	\N	\N	received
163	-4833184749	6238913206	48	pressure	\N	\N	18.41	16.74	2025-11-14 09:59:41.881286		44.058427	58.686851	2025-11-14 05:00:16.092149	\N	\N	\N	\N	\N	received
164	-4833184749	6238913206	61	pressure	\N	\N	18.17	16.84	2025-11-14 10:15:56.034906		44.075948	58.66386	2025-11-14 05:16:08.622532	\N	\N	\N	\N	\N	received
165	-4833184749	1042194608	89	pressure	\N	\N	17.21	16.51	2025-11-14 14:40:10.662487		44.019162	58.722882	2025-11-14 09:40:21.745522	\N	\N	\N	\N	\N	received
166	-4833184749	1042194608	43	pressure	\N	\N	17.16	16.47	2025-11-14 14:55:13.056088		44.050171	58.700023	2025-11-14 09:55:29.569836	\N	\N	\N	\N	\N	received
167	-4833184749	1042194608	43	reagent	1253	1	17.2	16.5	2025-11-14 15:02:11.927345		44.050082	58.699759	2025-11-14 10:02:23.340797	\N	\N	\N	\N	\N	received
168	-4833184749	6740275295	48	pressure	\N	\N	18.17	16.45	2025-11-14 15:27:50.019442		44.05842	58.686895	2025-11-14 10:28:08.344052	\N	\N	\N	\N	\N	received
169	-4833184749	6238913206	43	reagent	Oil Foam	1	17.58	17.01	2025-11-14 23:38:54.837478		44.050193	58.699702	2025-11-14 18:39:09.003179	\N	\N	\N	\N	\N	received
170	-4833184749	6740275295	89	reagent	1259	1	17.74	17.18	2025-11-14 23:57:46.775591		44.019077	58.72269	2025-11-14 18:58:01.206708	\N	\N	\N	\N	\N	received
171	-4833184749	1042194608	48	pressure	\N	\N	18.46	16.89	2025-11-15 00:42:57.585996		\N	\N	2025-11-14 19:43:07.191027	\N	\N	\N	\N	\N	skipped_by_user
172	-4833184749	1042194608	87	pressure	\N	\N	19.12	18.52	2025-11-15 00:43:38.355092		\N	\N	2025-11-14 19:43:47.437129	\N	\N	\N	\N	\N	skipped_by_user
173	-4833184749	1042194608	61	pressure	\N	\N	18.17	17.02	2025-11-15 00:44:22.561962		\N	\N	2025-11-14 19:44:34.836455	\N	\N	\N	\N	\N	skipped_by_user
174	-4833184749	1042194608	61	pressure	\N	\N	17.84	16.69	2025-11-15 08:31:35.182915		\N	\N	2025-11-15 03:31:44.032047	\N	\N	\N	\N	\N	skipped_by_user
175	-4833184749	6238913206	48	pressure	\N	\N	18.48	16.81	2025-11-15 08:31:37.16077		\N	\N	2025-11-15 03:31:51.567897	\N	\N	\N	\N	\N	skipped_by_user
176	-4833184749	6740275295	89	reagent	1259	1	17.73	16.8	2025-11-15 09:22:48.169866		44.019201	58.722849	2025-11-15 04:23:04.192461	\N	\N	\N	\N	\N	received
177	-4833184749	6238913206	43	reagent	Liquid Foam	1	17.24	16.66	2025-11-15 09:40:02.494378		44.050157	58.69962	2025-11-15 04:40:16.445086	\N	\N	\N	\N	\N	received
178	-4833184749	6238913206	87	equip	\N	\N	\N	\N	2025-11-15 14:55:50.319275	Временно остановка скв, для замена задвижки и установка шлюз	44.059859	58.69639	2025-11-15 09:57:00.682626	other	{}	Для замена задвижки и утановка шлюз	\N	\N	received
179	-4833184749	6238913206	87	purge	\N	\N	31.25	20.38	2025-11-15 16:37:05.840955		44.059811	58.696468	2025-11-15 11:37:58.798459	\N	\N	\N	stop	\N	received
180	-4833184749	6238913206	87	equip	\N	\N	\N	\N	2025-11-15 16:46:19.627733	Сняли со скв 131	\N	\N	2025-11-15 11:47:03.43879	gate	{}	\N	\N	\N	skipped_by_user
181	-4833184749	6238913206	87	reagent	1253	1	21.23	20.61	2025-11-15 17:32:54.834949		44.059768	58.696429	2025-11-15 12:33:07.827327	\N	\N	\N	\N	\N	received
183	-4833184749	1042194608	43	reagent	SW-OF	1	17.43	16.82	2025-11-15 21:22:27.078846		44.050154	58.699736	2025-11-15 16:22:39.631494	\N	\N	\N	\N	\N	received
184	-4833184749	1042194608	87	reagent	Oil Foam	1	20.25	19.62	2025-11-15 21:32:08.548855		44.059827	58.696379	2025-11-15 16:32:19.125035	\N	\N	\N	\N	\N	received
185	-4833184749	6740275295	48	pressure	\N	\N	18.64	16.95	2025-11-15 21:44:17.427367		44.058335	58.686703	2025-11-15 16:44:33.25825	\N	\N	\N	\N	\N	received
187	-4833184749	6740275295	89	reagent	1259	1	17.44	16.95	2025-11-16 09:03:40.430029		44.019149	58.722855	2025-11-16 04:04:00.350329	\N	\N	\N	\N	\N	received
188	-4833184749	1042194608	43	reagent	SW-OF	1	17.09	16.45	2025-11-16 09:21:47.738775		44.050154	58.699736	2025-11-16 04:22:01.725419	\N	\N	\N	\N	\N	received
189	-4833184749	6740275295	87	reagent	Super Foam	1	21.62	21.2	2025-11-16 09:32:50.703342		44.059965	58.696262	2025-11-16 04:33:04.676955	\N	\N	\N	\N	\N	received
190	-4833184749	6238913206	87	purge	\N	\N	22.94	22.63	2025-11-16 14:01:49.620206		44.059835	58.696335	2025-11-16 09:02:14.96287	\N	\N	\N	start	\N	received
191	-4833184749	6238913206	87	purge	\N	\N	1.25	22.75	2025-11-16 14:56:38.632572		44.059891	58.696401	2025-11-16 09:57:08.13335	\N	\N	\N	press	\N	received
192	-4833184749	6238913206	87	purge	\N	\N	25.62	22.65	2025-11-16 15:13:28.480869		44.059878	58.696373	2025-11-16 10:14:27.630353	\N	\N	\N	stop	\N	received
193	-4833184749	6238913206	61	pressure	\N	\N	18.56	17.01	2025-11-16 15:51:11.857526		\N	\N	2025-11-16 10:51:27.197791	\N	\N	\N	\N	\N	skipped_by_user
194	-4833184749	6238913206	43	pressure	\N	\N	17.1	16.43	2025-11-16 15:57:36.79238		\N	\N	2025-11-16 10:57:55.08277	\N	\N	\N	\N	\N	skipped_by_user
195	-4833184749	6238913206	48	pressure	\N	\N	18.31	16.56	2025-11-16 15:58:43.268431		\N	\N	2025-11-16 10:58:54.779621	\N	\N	\N	\N	\N	skipped_by_user
196	-4833184749	6238913206	87	pressure	\N	\N	23.54	22.85	2025-11-16 16:00:21.844723		\N	\N	2025-11-16 11:01:04.974012	\N	\N	\N	\N	\N	timeout
197	-4833184749	6238913206	89	reagent	1259	1	17.35	16.91	2025-11-16 20:51:38.945583		44.019177	58.722794	2025-11-16 15:51:53.052443	\N	\N	\N	\N	\N	received
198	-4833184749	6238913206	43	reagent	SW-OF	1	17.26	16.51	2025-11-16 21:13:30.10833		44.050213	58.699686	2025-11-16 16:13:43.246924	\N	\N	\N	\N	\N	received
200	-4833184749	6740275295	48	pressure	\N	\N	18.45	16.73	2025-11-16 22:00:48.382483		\N	\N	2025-11-16 17:01:01.941451	\N	\N	\N	\N	\N	skipped_by_user
201	-4833184749	6740275295	61	pressure	\N	\N	18.67	17.28	2025-11-16 22:01:34.034938		\N	\N	2025-11-16 17:01:44.269445	\N	\N	\N	\N	\N	skipped_by_user
202	-4833184749	6238913206	89	reagent	1259	1	17.59	16.94	2025-11-17 09:19:35.508052		44.019177	58.722794	2025-11-17 04:19:47.246054	\N	\N	\N	\N	\N	received
203	-4833184749	6238913206	89	other	\N	\N	\N	\N	2025-11-17 09:23:32.253959	Ршл до 16.94 после 16.88	44.019181	58.722866	2025-11-17 04:25:01.279898	\N	\N	\N	\N	\N	received
204	-4833184749	6238913206	43	reagent	SW-OF	1	17.26	16.66	2025-11-17 09:43:40.918313		44.050298	58.700066	2025-11-17 04:43:53.049984	\N	\N	\N	\N	\N	received
186	-4833184749	6740275295	61	reagent	Oil Foam	1	17.87	17.13	2025-11-15 22:08:08.479116		44.075987	58.663827	2025-11-15 17:08:22.315509	\N	\N	\N	\N	\N	received
199	-4833184749	6238913206	87	reagent	1253	1	23.14	22.34	2025-11-16 21:25:01.651275		44.059827	58.696379	2025-11-16 16:25:15.258465	\N	\N	\N	\N	\N	received
205	-4833184749	6740275295	87	reagent	1253	1	22.3	21.51	2025-11-17 09:57:44.528031		44.059712	58.696364	2025-11-17 04:57:59.275098	\N	\N	\N	\N	\N	received
206	-4833184749	6740275295	89	pressure	\N	\N	17.72	16.95	2025-11-17 13:23:31.577356		\N	\N	2025-11-17 08:23:46.065125	\N	\N	\N	\N	\N	skipped_by_user
207	-4833184749	6238913206	87	reagent	Oil Foam	1	22.91	21.95	2025-11-17 13:23:46.052087		44.05991	58.696384	2025-11-17 08:24:05.322929	\N	\N	\N	\N	\N	received
208	-4833184749	6740275295	48	pressure	\N	\N	18.43	16.59	2025-11-17 13:24:44.269079		\N	\N	2025-11-17 08:24:59.936294	\N	\N	\N	\N	\N	skipped_by_user
209	-4833184749	6740275295	43	pressure	\N	\N	17.22	16.56	2025-11-17 13:28:21.316048		\N	\N	2025-11-17 08:28:34.338435	\N	\N	\N	\N	\N	skipped_by_user
210	-4833184749	6740275295	61	pressure	\N	\N	18.52	17.1	2025-11-17 13:29:18.122957		\N	\N	2025-11-17 08:29:57.917649	\N	\N	\N	\N	\N	timeout
211	-4833184749	6238913206	89	reagent	1259	1	17.21	16.74	2025-11-17 21:03:20.802116		44.019157	58.722811	2025-11-17 16:03:33.147284	\N	\N	\N	\N	\N	received
212	-4833184749	6238913206	87	reagent	1253	1	22.57	21.4	2025-11-17 21:27:05.807978		44.059823	58.696307	2025-11-17 16:27:18.880844	\N	\N	\N	\N	\N	received
213	-4833184749	6238913206	43	reagent	SW-OF	2	17.53	16.91	2025-11-17 21:38:37.344527		44.050082	58.699759	2025-11-17 16:38:49.179848	\N	\N	\N	\N	\N	received
214	-4833184749	6238913206	48	reagent	1253	1	18.29	16.95	2025-11-17 21:53:34.95057		44.058352	58.686802	2025-11-17 16:53:49.821593	\N	\N	\N	\N	\N	received
215	-4833184749	6238913206	61	reagent	Oil Foam	1	18.41	17.05	2025-11-17 22:22:01.304495		44.075852	58.663828	2025-11-17 17:22:16.029207	\N	\N	\N	\N	\N	received
216	-4833184749	6238913206	89	other	\N	\N	\N	\N	2025-11-18 09:33:40.159996	Перезагрузка монометр, до Ршл 16.35 после Ршл 17.07	44.019137	58.722827	2025-11-18 04:34:37.886445	\N	\N	\N	\N	\N	received
217	-4833184749	6238913206	89	reagent	1253	1	17.69	17.07	2025-11-18 09:35:37.548954		44.019197	58.722777	2025-11-18 04:35:49.68159	\N	\N	\N	\N	\N	received
218	-4833184749	6238913206	87	pressure	\N	\N	21.55	20.39	2025-11-18 09:58:03.445211		44.059807	58.696396	2025-11-18 04:58:14.526007	\N	\N	\N	\N	\N	received
219	-4833184749	6238913206	43	reagent	SW-OF	1	17.39	16.6	2025-11-18 10:09:28.339093		44.050215	58.700061	2025-11-18 05:09:41.323292	\N	\N	\N	\N	\N	received
220	-4833184749	6740275295	48	pressure	\N	\N	18.14	16.97	2025-11-18 10:55:05.389523		\N	\N	2025-11-18 05:55:16.221984	\N	\N	\N	\N	\N	skipped_by_user
221	-4833184749	6740275295	61	pressure	\N	\N	18.34	17.15	2025-11-18 10:56:01.207507		\N	\N	2025-11-18 05:56:10.608883	\N	\N	\N	\N	\N	skipped_by_user
222	-4833184749	6740275295	89	pressure	\N	\N	18.23	17.24	2025-11-18 12:54:06.485235		\N	\N	2025-11-18 07:54:24.837233	\N	\N	\N	\N	\N	skipped_by_user
223	-4833184749	6740275295	107	pressure	\N	\N	22.66	21.66	2025-11-18 12:55:11.492799		\N	\N	2025-11-18 07:55:21.371233	\N	\N	\N	\N	\N	skipped_by_user
224	-4833184749	6740275295	48	pressure	\N	\N	18.04	16.79	2025-11-18 12:55:54.735392		\N	\N	2025-11-18 07:56:02.914809	\N	\N	\N	\N	\N	skipped_by_user
225	-4833184749	6740275295	43	pressure	\N	\N	17.49	16.7	2025-11-18 12:56:35.14308		\N	\N	2025-11-18 07:56:46.06914	\N	\N	\N	\N	\N	skipped_by_user
226	-4833184749	6740275295	61	pressure	\N	\N	18.45	17.12	2025-11-18 12:57:07.284926		\N	\N	2025-11-18 07:57:17.430103	\N	\N	\N	\N	\N	skipped_by_user
182	-4833184749	6740275295	89	reagent	1259	1	17.36	16.91	2025-11-15 20:58:07.822679		44.01913	58.722872	2025-11-15 15:58:30.276335	\N	\N	\N	\N	\N	received
227	-4833184749	6238913206	48	reagent	Super Foam	1	18.01	17.24	2025-11-18 20:21:23.070642		44.058403	58.686796	2025-11-18 15:21:34.732368	\N	\N	\N	\N	\N	received
228	-4833184749	6238913206	87	reagent	1253	1	22.37	21.35	2025-11-18 21:28:07.747298		44.059871	58.696417	2025-11-18 16:28:22.180067	\N	\N	\N	\N	\N	received
229	-4833184749	6238913206	43	reagent	SW-OF	1	17.79	17.17	2025-11-18 21:38:05.923147		44.050157	58.69962	2025-11-18 16:38:19.075692	\N	\N	\N	\N	\N	received
230	-4833184749	6238913206	43	reagent	SW-OF	1	17.79	17.17	2025-11-18 21:42:53.504398		44.050126	58.699797	2025-11-18 16:43:08.901722	\N	\N	\N	\N	\N	received
231	-4833184749	6238913206	89	reagent	1251	2	17.52	17.28	2025-11-18 22:03:24.899907		44.019157	58.722811	2025-11-18 17:03:37.770972	\N	\N	\N	\N	\N	received
232	-4833184749	6238913206	48	reagent	1251	1	17.45	16.92	2025-11-18 23:37:54.581004		44.058427	58.686851	2025-11-18 18:38:08.92328	\N	\N	\N	\N	\N	received
233	-4833184749	6238913206	89	reagent	1251	1	17.89	17.21	2025-11-19 09:33:42.40847		44.019181	58.722866	2025-11-19 04:33:54.271888	\N	\N	\N	\N	\N	received
234	-4833184749	6740275295	87	pressure	\N	\N	21.13	19.86	2025-11-19 09:54:17.618028		44.059755	58.696402	2025-11-19 04:54:32.214505	\N	\N	\N	\N	\N	received
235	-4833184749	6238913206	48	reagent	1251	1	18.01	16.96	2025-11-19 10:00:16.056025		44.058376	58.686857	2025-11-19 05:00:27.822183	\N	\N	\N	\N	\N	received
236	-4833184749	6740275295	43	purge	\N	\N	17.55	16.86	2025-11-19 10:13:36.63242		44.050122	58.699725	2025-11-19 05:14:33.970091	\N	\N	\N	start	\N	received
237	-4833184749	6238913206	43	purge	\N	\N	0	16.57	2025-11-19 11:22:23.259029		44.050162	58.699879	2025-11-19 06:22:55.150463	\N	\N	\N	press	\N	received
238	-4833184749	6238913206	43	purge	\N	\N	18.95	16.52	2025-11-19 12:01:49.939612		44.05035	58.70006	2025-11-19 07:02:42.85079	\N	\N	\N	stop	\N	received
239	-4833184749	6238913206	43	reagent	Oil Foam	1	17.15	16.65	2025-11-19 12:13:38.592307		44.050223	58.700017	2025-11-19 07:13:50.024502	\N	\N	\N	\N	\N	received
240	-4833184749	6238913206	43	reagent	1251	1	17.13	16.73	2025-11-19 14:18:55.532471		44.050193	58.699702	2025-11-19 09:19:08.075798	\N	\N	\N	\N	\N	received
241	-4833184749	6238913206	89	pressure	\N	\N	17.05	15.05	2025-11-19 20:47:57.659154		44.019078	58.722877	2025-11-19 15:48:14.100844	\N	\N	\N	\N	\N	received
242	-4833184749	6238913206	43	reagent	SW-OF	1	17.14	16.42	2025-11-19 21:08:48.870684		44.050063	58.699775	2025-11-19 16:09:01.505736	\N	\N	\N	\N	\N	received
243	-4833184749	6238913206	87	pressure	\N	\N	18.23	16.35	2025-11-19 21:18:56.201211		44.059783	58.696341	2025-11-19 16:19:08.663978	\N	\N	\N	\N	\N	received
244	-4833184749	6238913206	48	reagent	1253	1	17.5	16.42	2025-11-19 21:28:02.879747		44.058431	58.686735	2025-11-19 16:28:17.048364	\N	\N	\N	\N	\N	received
245	-4833184749	6238913206	61	reagent	Oil Foam	1	17.82	17.04	2025-11-19 21:50:27.082106		44.07584	58.6638	2025-11-19 16:50:39.9822	\N	\N	\N	\N	\N	received
246	-4833184749	6238913206	89	reagent	1259	1	17.83	17.61	2025-11-20 08:31:23.037288		44.019162	58.722882	2025-11-20 03:31:34.536164	\N	\N	\N	\N	\N	received
247	-4833184749	6238913206	89	reagent	1259	1	17.25	17.22	2025-11-20 09:06:01.975157		44.01913	58.722872	2025-11-20 04:06:13.69947	\N	\N	\N	\N	\N	received
248	-4833184749	6238913206	43	reagent	SW-OF	1	16.3	15.78	2025-11-20 09:45:43.661496		44.050198	58.699774	2025-11-20 04:45:56.19259	\N	\N	\N	\N	\N	received
249	-4833184749	6238913206	43	reagent	1259	1	16.3	15.78	2025-11-20 09:46:47.425879		44.050146	58.69978	2025-11-20 04:46:58.65037	\N	\N	\N	\N	\N	received
250	-4833184749	6740275295	61	pressure	\N	\N	17.73	16.06	2025-11-20 10:31:34.823779		\N	\N	2025-11-20 05:31:48.163242	\N	\N	\N	\N	\N	skipped_by_user
251	-4833184749	6740275295	48	pressure	\N	\N	17.04	15.87	2025-11-20 10:34:20.085434		\N	\N	2025-11-20 05:34:28.13595	\N	\N	\N	\N	\N	skipped_by_user
252	-4833184749	6740275295	87	pressure	\N	\N	17.48	15.73	2025-11-20 10:34:55.40199		\N	\N	2025-11-20 05:35:04.7152	\N	\N	\N	\N	\N	skipped_by_user
253	-4833184749	6740275295	61	pressure	\N	\N	17.7	16.05	2025-11-20 14:30:54.12837		\N	\N	2025-11-20 09:31:02.803544	\N	\N	\N	\N	\N	skipped_by_user
254	-4833184749	6740275295	43	pressure	\N	\N	16.77	15.73	2025-11-20 14:31:26.61089		\N	\N	2025-11-20 09:31:34.713617	\N	\N	\N	\N	\N	skipped_by_user
255	-4833184749	6740275295	48	pressure	\N	\N	17.12	15.84	2025-11-20 14:31:55.556105		\N	\N	2025-11-20 09:32:05.328554	\N	\N	\N	\N	\N	skipped_by_user
256	-4833184749	6740275295	87	pressure	\N	\N	17.34	15.68	2025-11-20 14:32:24.333755		\N	\N	2025-11-20 09:32:32.405028	\N	\N	\N	\N	\N	skipped_by_user
257	-4833184749	6740275295	89	pressure	\N	\N	19.14	18.48	2025-11-20 14:32:55.057628		\N	\N	2025-11-20 09:33:04.773031	\N	\N	\N	\N	\N	skipped_by_user
258	-4833184749	6238913206	89	reagent	1259	1	18.24	17.24	2025-11-20 20:49:20.267999		44.019031	58.722955	2025-11-20 15:49:37.423959	\N	\N	\N	\N	\N	received
259	-4833184749	6238913206	43	reagent	SW-OF	1	16.61	15.57	2025-11-20 21:29:35.166305		44.050086	58.699642	2025-11-20 16:29:47.48649	\N	\N	\N	\N	\N	received
260	-4833184749	6740275295	61	pressure	\N	\N	17.3	15.62	2025-11-20 22:11:49.62786		\N	\N	2025-11-20 17:12:00.080903	\N	\N	\N	\N	\N	skipped_by_user
261	-4833184749	6740275295	48	pressure	\N	\N	16.93	15.55	2025-11-20 22:13:01.768017		\N	\N	2025-11-20 17:13:11.531363	\N	\N	\N	\N	\N	skipped_by_user
262	-4833184749	6740275295	87	pressure	\N	\N	17.07	15.4	2025-11-20 22:13:54.696174		\N	\N	2025-11-20 17:14:05.825969	\N	\N	\N	\N	\N	skipped_by_user
263	-4833184749	6238913206	89	reagent	1259	1	17.77	17.14	2025-11-21 09:22:32.492345		44.019098	58.722861	2025-11-21 04:23:08.380733	\N	\N	\N	\N	\N	received
264	-4833184749	6238913206	43	reagent	SW-OF	1	16.4	15.42	2025-11-21 09:50:16.258136		44.050162	58.699879	2025-11-21 04:50:29.96358	\N	\N	\N	\N	\N	received
265	-4833184749	6238913206	61	pressure	\N	\N	17.36	15.79	2025-11-21 10:47:52.574584		\N	\N	2025-11-21 05:48:01.71842	\N	\N	\N	\N	\N	skipped_by_user
266	-4833184749	6238913206	48	pressure	\N	\N	16.95	15.43	2025-11-21 10:48:34.494703		\N	\N	2025-11-21 05:48:43.606027	\N	\N	\N	\N	\N	skipped_by_user
267	-4833184749	6238913206	87	pressure	\N	\N	16.9	15.31	2025-11-21 10:52:41.034563		\N	\N	2025-11-21 05:53:01.108391	\N	\N	\N	\N	\N	skipped_by_user
268	-4833184749	6740275295	89	pressure	\N	\N	18.66	18.05	2025-11-21 13:59:00.649867		\N	\N	2025-11-21 08:59:09.769379	\N	\N	\N	\N	\N	skipped_by_user
269	-4833184749	6740275295	87	pressure	\N	\N	17.31	15.92	2025-11-21 13:59:52.235685		\N	\N	2025-11-21 09:00:01.657083	\N	\N	\N	\N	\N	skipped_by_user
270	-4833184749	6740275295	48	pressure	\N	\N	17.46	16.02	2025-11-21 14:00:39.191886		\N	\N	2025-11-21 09:00:47.505141	\N	\N	\N	\N	\N	skipped_by_user
271	-4833184749	6740275295	43	pressure	\N	\N	17.62	15.94	2025-11-21 14:01:24.329366		\N	\N	2025-11-21 09:01:33.09868	\N	\N	\N	\N	\N	skipped_by_user
272	-4833184749	6740275295	61	pressure	\N	\N	17.46	16.02	2025-11-21 14:02:17.945267		\N	\N	2025-11-21 09:02:26.404301	\N	\N	\N	\N	\N	skipped_by_user
273	-4833184749	6238913206	89	reagent	1259	1	17.51	16.91	2025-11-21 21:11:47.991547		44.019189	58.722821	2025-11-21 16:12:02.154759	\N	\N	\N	\N	\N	received
274	-4833184749	6238913206	43	reagent	SW-OF	1	16.87	15.58	2025-11-21 21:29:03.235579		44.050186	58.699747	2025-11-21 16:29:14.61556	\N	\N	\N	\N	\N	received
275	-4833184749	6238913206	61	pressure	\N	\N	17.84	16.07	2025-11-21 21:49:59.334352		\N	\N	2025-11-21 16:50:10.011207	\N	\N	\N	\N	\N	skipped_by_user
276	-4833184749	6238913206	48	pressure	\N	\N	17.21	15.62	2025-11-21 21:51:12.977737		\N	\N	2025-11-21 16:51:23.922176	\N	\N	\N	\N	\N	skipped_by_user
277	-4833184749	6238913206	87	pressure	\N	\N	17.01	15.51	2025-11-21 21:52:01.371752		\N	\N	2025-11-21 16:52:11.304997	\N	\N	\N	\N	\N	skipped_by_user
278	-4833184749	6238913206	89	reagent	1259	1	18.87	18.63	2025-11-22 09:28:12.457254		44.019133	58.722756	2025-11-22 04:28:25.102551	\N	\N	\N	\N	\N	received
279	-4833184749	6238913206	43	reagent	SW-OF	1	17.2	16.02	2025-11-22 09:47:02.072254		44.050211	58.69999	2025-11-22 04:47:12.689734	\N	\N	\N	\N	\N	received
280	-4833184749	6238913206	87	reagent	1253	1	17.27	15.96	2025-11-22 10:46:26.985831		44.059839	58.696407	2025-11-22 05:46:38.804523	\N	\N	\N	\N	\N	received
281	-4833184749	6238913206	48	reagent	1253	1	17.63	15.96	2025-11-22 11:02:59.784526		44.058435	58.686807	2025-11-22 06:03:10.906271	\N	\N	\N	\N	\N	received
282	-4833184749	6238913206	61	reagent	Oil Foam	1	17.69	16.21	2025-11-22 11:20:04.764776		44.075896	58.663866	2025-11-22 06:20:16.103447	\N	\N	\N	\N	\N	received
283	-4833184749	6740275295	61	pressure	\N	\N	17.73	16.24	2025-11-22 14:57:30.580951		\N	\N	2025-11-22 09:57:47.321642	\N	\N	\N	\N	\N	skipped_by_user
284	-4833184749	6740275295	43	pressure	\N	\N	17.18	15.96	2025-11-22 14:58:20.3971		\N	\N	2025-11-22 09:58:27.926236	\N	\N	\N	\N	\N	skipped_by_user
285	-4833184749	6740275295	48	pressure	\N	\N	17.17	16.04	2025-11-22 14:59:03.607327		\N	\N	2025-11-22 09:59:11.546702	\N	\N	\N	\N	\N	skipped_by_user
286	-4833184749	6740275295	87	pressure	\N	\N	17.72	16.02	2025-11-22 14:59:45.976917		\N	\N	2025-11-22 09:59:54.988282	\N	\N	\N	\N	\N	skipped_by_user
287	-4833184749	6740275295	89	pressure	\N	\N	20.49	20.11	2025-11-22 15:00:29.882763		\N	\N	2025-11-22 10:00:42.254226	\N	\N	\N	\N	\N	skipped_by_user
288	-4833184749	6238913206	89	reagent	1259	1	19.4	18.7	2025-11-22 20:44:18.533155		44.019166	58.722954	2025-11-22 15:44:30.714922	\N	\N	\N	\N	\N	received
289	-4833184749	6238913206	43	reagent	SW-OF	1	17.63	16.32	2025-11-22 20:57:47.544924		44.050254	58.700028	2025-11-22 15:57:59.15301	\N	\N	\N	\N	\N	received
290	-4833184749	6238913206	87	pressure	\N	\N	17.87	16.3	2025-11-22 21:15:49.77904		\N	\N	2025-11-22 16:16:00.869304	\N	\N	\N	\N	\N	skipped_by_user
291	-4833184749	6238913206	48	pressure	\N	\N	17.61	16.36	2025-11-22 21:16:35.389586		\N	\N	2025-11-22 16:17:23.07125	\N	\N	\N	\N	\N	timeout
292	-4833184749	6238913206	61	pressure	\N	\N	17.99	16.51	2025-11-22 21:18:07.347086		\N	\N	2025-11-22 16:18:17.210493	\N	\N	\N	\N	\N	skipped_by_user
293	-4833184749	6238913206	43	other	\N	\N	\N	\N	2025-11-23 08:48:19.930442	Перезагрузка монометр 43 до Ршл 17.55 после 17.50	44.050274	58.700011	2025-11-23 03:49:11.791523	\N	\N	\N	\N	\N	received
294	-4833184749	6238913206	43	reagent	SW-OF	1	17.5	16.22	2025-11-23 08:50:40.81858		44.050169	58.699647	2025-11-23 03:50:51.743987	\N	\N	\N	\N	\N	received
295	-4833184749	6238913206	89	reagent	SW-OF	1	18.16	17.61	2025-11-23 09:07:13.110094		44.019177	58.722794	2025-11-23 04:07:26.630635	\N	\N	\N	\N	\N	received
296	-4833184749	6740275295	87	pressure	\N	\N	17.57	16.12	2025-11-23 09:25:13.367879		\N	\N	2025-11-23 04:25:21.774291	\N	\N	\N	\N	\N	skipped_by_user
297	-4833184749	6740275295	48	pressure	\N	\N	17.37	16.19	2025-11-23 09:25:41.610763		\N	\N	2025-11-23 04:25:49.282611	\N	\N	\N	\N	\N	skipped_by_user
298	-4833184749	6740275295	61	pressure	\N	\N	17.86	16.4	2025-11-23 09:26:08.160003		\N	\N	2025-11-23 04:26:16.393932	\N	\N	\N	\N	\N	skipped_by_user
299	-4833184749	6238913206	89	pressure	\N	\N	19.29	18.7	2025-11-23 13:39:03.054719		\N	\N	2025-11-23 08:39:13.907948	\N	\N	\N	\N	\N	skipped_by_user
300	-4833184749	6238913206	87	pressure	\N	\N	17.4	15.95	2025-11-23 13:39:48.368615		\N	\N	2025-11-23 08:39:57.063595	\N	\N	\N	\N	\N	skipped_by_user
301	-4833184749	6238913206	43	pressure	\N	\N	17.38	16.07	2025-11-23 13:40:29.557153		\N	\N	2025-11-23 08:40:38.377995	\N	\N	\N	\N	\N	skipped_by_user
302	-4833184749	6238913206	48	pressure	\N	\N	17.37	16.07	2025-11-23 13:43:47.114321		\N	\N	2025-11-23 08:43:58.135837	\N	\N	\N	\N	\N	skipped_by_user
303	-4833184749	6238913206	61	pressure	\N	\N	17.67	16.23	2025-11-23 13:44:32.600485		\N	\N	2025-11-23 08:44:48.804578	\N	\N	\N	\N	\N	skipped_by_user
304	-4833184749	6238913206	89	reagent	1259	1	17.3	16.8	2025-11-23 21:16:28.804726		44.019101	58.722745	2025-11-23 16:16:50.716814	\N	\N	\N	\N	\N	received
305	-4833184749	6238913206	43	reagent	SW-OF	1	16.86	15.49	2025-11-23 21:43:38.807462		44.050198	58.699774	2025-11-23 16:43:50.957396	\N	\N	\N	\N	\N	received
306	-4833184749	6740275295	61	pressure	\N	\N	17.27	15.83	2025-11-23 22:07:55.090782		\N	\N	2025-11-23 17:08:05.319919	\N	\N	\N	\N	\N	skipped_by_user
307	-4833184749	6740275295	48	pressure	\N	\N	17.11	15.54	2025-11-23 22:08:48.09835		\N	\N	2025-11-23 17:08:57.726729	\N	\N	\N	\N	\N	skipped_by_user
308	-4833184749	6740275295	87	pressure	\N	\N	16.93	15.39	2025-11-23 22:09:35.651131		\N	\N	2025-11-23 17:09:44.63939	\N	\N	\N	\N	\N	skipped_by_user
309	-4833184749	6238913206	89	reagent	1259	1	17.45	17.04	2025-11-24 09:27:07.888725		44.019197	58.722777	2025-11-24 04:27:19.591396	\N	\N	\N	\N	\N	received
310	-4833184749	6740275295	87	pressure	\N	\N	16.89	15.43	2025-11-24 09:58:36.662295		\N	\N	2025-11-24 04:58:45.382639	\N	\N	\N	\N	\N	skipped_by_user
311	-4833184749	6740275295	48	pressure	\N	\N	17.3	15.53	2025-11-24 09:59:25.150546		\N	\N	2025-11-24 04:59:32.035552	\N	\N	\N	\N	\N	skipped_by_user
312	-4833184749	6740275295	43	pressure	\N	\N	16.96	15.56	2025-11-24 09:59:54.85725		\N	\N	2025-11-24 05:00:02.611449	\N	\N	\N	\N	\N	skipped_by_user
313	-4833184749	6740275295	61	pressure	\N	\N	17.46	16.05	2025-11-24 10:00:27.316295		\N	\N	2025-11-24 05:00:35.364428	\N	\N	\N	\N	\N	skipped_by_user
314	-4833184749	6238913206	89	reagent	1259	1	17.51	16.86	2025-11-24 20:50:40.695933		44.019201	58.722849	2025-11-24 15:50:51.739771	\N	\N	\N	\N	\N	received
315	-4833184749	6238913206	43	reagent	SW-OF	1	17.41	16.11	2025-11-24 21:16:48.720567		44.050181	58.699675	2025-11-24 16:17:01.060964	\N	\N	\N	\N	\N	received
316	-4833184749	6238913206	87	reagent	1253	1	17.17	15.93	2025-11-24 21:30:39.67623		44.059763	58.696358	2025-11-24 16:30:51.44944	\N	\N	\N	\N	\N	received
317	-4833184749	6740275295	48	pressure	\N	\N	17.33	15.79	2025-11-24 22:32:47.715693		\N	\N	2025-11-24 17:32:57.746392	\N	\N	\N	\N	\N	skipped_by_user
318	-4833184749	6740275295	61	pressure	\N	\N	17.49	16.13	2025-11-24 22:33:17.526673		\N	\N	2025-11-24 17:33:25.597297	\N	\N	\N	\N	\N	skipped_by_user
319	-4833184749	6238913206	89	reagent	1259	1	17.44	16.9	2025-11-25 08:20:16.209626		44.019137	58.722827	2025-11-25 03:20:32.487732	\N	\N	\N	\N	\N	received
320	-4833184749	6238913206	61	pressure	\N	\N	17.7	16.32	2025-11-25 08:49:57.501278		\N	\N	2025-11-25 03:50:14.551987	\N	\N	\N	\N	\N	skipped_by_user
321	-4833184749	6238913206	43	pressure	\N	\N	17.18	15.88	2025-11-25 08:50:53.56267		\N	\N	2025-11-25 03:51:04.241972	\N	\N	\N	\N	\N	skipped_by_user
322	-4833184749	6238913206	48	pressure	\N	\N	17.63	15.91	2025-11-25 08:51:39.126113		\N	\N	2025-11-25 03:52:11.83544	\N	\N	\N	\N	\N	skipped_by_user
323	-4833184749	6238913206	87	pressure	\N	\N	17.23	15.88	2025-11-25 08:52:52.213718		\N	\N	2025-11-25 03:53:02.146839	\N	\N	\N	\N	\N	skipped_by_user
324	-4833184749	7392840491	89	reagent	1253	1	18.35	17.99	2025-11-25 16:17:11.196461		44.019157	58.722811	2025-11-25 11:17:28.44793	\N	\N	\N	\N	\N	received
325	-4833184749	7392840491	43	pressure	\N	\N	17.15	15.78	2025-11-25 16:36:17.280275		44.050174	58.699719	2025-11-25 11:36:33.210835	\N	\N	\N	\N	\N	received
326	-4833184749	7392840491	87	pressure	\N	\N	17.11	15.78	2025-11-25 16:50:54.15105		44.059922	58.696412	2025-11-25 11:51:12.35047	\N	\N	\N	\N	\N	received
327	-4833184749	1042194608	48	pressure	\N	\N	17.35	15.83	2025-11-25 16:55:47.066958		44.058443	58.686762	2025-11-25 11:55:58.70903	\N	\N	\N	\N	\N	received
328	-4833184749	1042194608	61	pressure	\N	\N	17.58	16.15	2025-11-25 17:13:27.458691		\N	\N	2025-11-25 12:13:57.178024	\N	\N	\N	\N	\N	timeout
329	-4833184749	7392840491	89	reagent	1253	1	17.42	16.85	2025-11-25 22:10:42.959396		44.019106	58.722816	2025-11-25 17:11:04.140725	\N	\N	\N	\N	\N	received
330	-4833184749	1042194608	87	pressure	\N	\N	16.84	15.49	2025-11-25 22:47:41.042446		\N	\N	2025-11-25 17:47:52.181716	\N	\N	\N	\N	\N	skipped_by_user
331	-4833184749	1042194608	48	pressure	\N	\N	16.98	15.51	2025-11-25 22:48:33.000551		\N	\N	2025-11-25 17:48:42.278194	\N	\N	\N	\N	\N	skipped_by_user
332	-4833184749	1042194608	43	pressure	\N	\N	16.86	15.49	2025-11-25 22:49:15.047026		\N	\N	2025-11-25 17:49:25.26033	\N	\N	\N	\N	\N	skipped_by_user
333	-4833184749	1042194608	61	pressure	\N	\N	17.77	16.05	2025-11-25 22:50:00.334664		\N	\N	2025-11-25 17:50:08.442166	\N	\N	\N	\N	\N	skipped_by_user
334	-4833184749	7392840491	89	reagent	1253	1	16.97	16.76	2025-11-26 09:30:54.284461		44.01907	58.722922	2025-11-26 04:31:09.186973	\N	\N	\N	\N	\N	received
335	-4833184749	7392840491	43	reagent	SW-OF	1	16.9	15.55	2025-11-26 10:11:51.447102		44.050162	58.699692	2025-11-26 05:12:03.990919	\N	\N	\N	\N	\N	received
336	-4833184749	1042194608	87	reagent	Super Foam	1	16.91	15.55	2025-11-26 10:20:11.014813		44.059636	58.696314	2025-11-26 05:20:21.896266	\N	\N	\N	\N	\N	received
337	-4833184749	1042194608	48	reagent	Super Foam	1	16.95	15.54	2025-11-26 10:26:05.660909		44.058403	58.686796	2025-11-26 05:26:16.863265	\N	\N	\N	\N	\N	received
338	-4833184749	1042194608	61	reagent	Super Foam	1	17.25	15.96	2025-11-26 10:43:18.572377		44.07582	58.663629	2025-11-26 05:44:23.600654	\N	\N	\N	\N	\N	received
339	-4833184749	1042194608	61	reagent	Super Foam	1	17.25	15.96	2025-11-26 10:43:18.572377		44.07582	58.663629	2025-11-26 05:44:23.611207	\N	\N	\N	\N	\N	received
391	-4833184749	7392840491	61	pressure	\N	\N	18.31	17.01	2025-11-28 22:16:07.830416		\N	\N	2025-11-28 17:16:30.975228	\N	\N	\N	\N	\N	skipped_by_user
340	-4833184749	1042194608	61	reagent	Super Foam	1	17.25	15.96	2025-11-26 10:43:18.572377		44.07582	58.663629	2025-11-26 05:44:28.356777	\N	\N	\N	\N	\N	received
341	-4833184749	7392840491	89	reagent	1259	1	18.11	18.09	2025-11-26 11:49:01.345019		44.019031	58.722955	2025-11-26 06:49:15.453065	\N	\N	\N	\N	\N	received
342	-4833184749	7392840491	89	reagent	1259	1	19.79	19.79	2025-11-26 17:31:24.978912		44.01905	58.722751	2025-11-26 12:31:38.099181	\N	\N	\N	\N	\N	received
343	-4833184749	7392840491	89	reagent	1253	1	19.63	19.66	2025-11-26 17:55:35.946708		44.019233	58.72286	2025-11-26 12:55:56.002221	\N	\N	\N	\N	\N	received
344	-4833184749	1042194608	87	pressure	\N	\N	17.57	16.02	2025-11-26 18:00:13.068248		\N	\N	2025-11-26 13:00:27.503071	\N	\N	\N	\N	\N	skipped_by_user
345	-4833184749	1042194608	48	pressure	\N	\N	17.12	16.08	2025-11-26 18:00:51.996332		\N	\N	2025-11-26 13:01:00.843461	\N	\N	\N	\N	\N	skipped_by_user
346	-4833184749	1042194608	43	pressure	\N	\N	17.24	15.98	2025-11-26 18:01:28.164505		\N	\N	2025-11-26 13:01:43.171347	\N	\N	\N	\N	\N	skipped_by_user
347	-4833184749	1042194608	61	pressure	\N	\N	17.8	16.58	2025-11-26 18:02:33.339568		\N	\N	2025-11-26 13:02:43.758853	\N	\N	\N	\N	\N	skipped_by_user
348	-4833184749	7392840491	89	pressure	\N	\N	19.21	19.25	2025-11-26 21:48:44.460458		44.019162	58.722882	2025-11-26 16:48:58.900528	\N	\N	\N	\N	\N	received
349	-4833184749	6730772526	89	other	\N	\N	\N	\N	2025-11-26 21:59:55.024901	В связи с переходом с мини ДКС на большой ДКС 3-го ГСП скв 89 давления на шлейфе выросло с 16кгс на 19кгс скважина остановилась, открываем на продувку	\N	\N	2025-11-26 17:01:51.832117	\N	\N	\N	\N	\N	skipped_by_user
350	-4833184749	1042194608	89	purge	\N	\N	19.4	19.25	2025-11-26 22:30:45.774492		44.019141	58.722711	2025-11-26 17:31:48.186176	\N	\N	\N	start	\N	received
351	-4833184749	7392840491	89	purge	\N	\N	2.1	19.17	2025-11-26 22:50:12.586036		\N	\N	2025-11-26 18:09:44.437937	\N	\N	\N	press	\N	timeout
352	-4833184749	7392840491	89	purge	\N	\N	31.06	19.19	2025-11-26 23:14:42.045689		44.019181	58.722866	2025-11-26 18:15:28.709356	\N	\N	\N	stop	\N	received
353	-4833184749	1042194608	89	reagent	1259	1	21.67	19.93	2025-11-26 23:36:22.358023		44.019169	58.722838	2025-11-26 18:36:35.998593	\N	\N	\N	\N	\N	received
354	-4833184749	1042194608	43	reagent	SW-OF	1	17.38	16.16	2025-11-26 23:53:59.930921		44.05021	58.699802	2025-11-26 18:54:15.118624	\N	\N	\N	\N	\N	received
355	-4833184749	7392840491	89	reagent	1259	1	19.29	18.44	2025-11-27 09:52:09.803248		44.019121	58.722728	2025-11-27 04:52:22.924986	\N	\N	\N	\N	\N	received
356	-4833184749	7392840491	43	reagent	SW-OF	1	17.81	16.64	2025-11-27 10:09:05.783027		44.049451	58.701725	2025-11-27 05:09:18.612031	\N	\N	\N	\N	\N	received
357	-4833184749	7392840491	87	pressure	\N	\N	17.85	16.64	2025-11-27 10:17:41.178236		44.059747	58.696258	2025-11-27 05:17:57.983598	\N	\N	\N	\N	\N	received
358	-4833184749	7392840491	48	reagent	Oil Foam	1	17.75	16.68	2025-11-27 10:24:58.756888		44.058482	58.686729	2025-11-27 05:25:10.7956	\N	\N	\N	\N	\N	received
359	-4833184749	7392840491	61	reagent	Oil Foam	1	18.21	16.95	2025-11-27 10:39:59.232452		44.075952	58.663932	2025-11-27 05:40:13.341441	\N	\N	\N	\N	\N	received
360	-4833184749	1042194608	89	pressure	\N	\N	18.73	17.86	2025-11-27 13:38:06.79493		\N	\N	2025-11-27 08:38:22.504788	\N	\N	\N	\N	\N	skipped_by_user
361	-4833184749	1042194608	87	pressure	\N	\N	17.67	16.47	2025-11-27 13:38:47.443641		\N	\N	2025-11-27 08:39:03.659499	\N	\N	\N	\N	\N	skipped_by_user
362	-4833184749	1042194608	48	pressure	\N	\N	17.36	16.51	2025-11-27 13:39:24.986549		\N	\N	2025-11-27 08:39:36.512401	\N	\N	\N	\N	\N	skipped_by_user
363	-4833184749	1042194608	43	pressure	\N	\N	17.66	16.44	2025-11-27 13:40:04.530054		\N	\N	2025-11-27 08:40:18.597333	\N	\N	\N	\N	\N	skipped_by_user
364	-4833184749	1042194608	61	pressure	\N	\N	18.35	16.85	2025-11-27 13:40:40.860461		\N	\N	2025-11-27 08:40:55.185908	\N	\N	\N	\N	\N	skipped_by_user
365	-4833184749	1042194608	89	pressure	\N	\N	18.1	17.81	2025-11-27 17:26:33.965383		\N	\N	2025-11-27 12:26:47.473711	\N	\N	\N	\N	\N	skipped_by_user
366	-4833184749	1042194608	87	pressure	\N	\N	17.61	16.34	2025-11-27 17:27:05.660306		\N	\N	2025-11-27 12:27:18.175943	\N	\N	\N	\N	\N	skipped_by_user
367	-4833184749	1042194608	48	pressure	\N	\N	17.43	16.41	2025-11-27 17:27:51.49107		\N	\N	2025-11-27 12:28:10.416703	\N	\N	\N	\N	\N	skipped_by_user
368	-4833184749	1042194608	43	pressure	\N	\N	17.51	16.29	2025-11-27 17:28:41.601121		\N	\N	2025-11-27 12:28:55.212559	\N	\N	\N	\N	\N	skipped_by_user
369	-4833184749	1042194608	61	pressure	\N	\N	18.12	16.67	2025-11-27 17:29:08.389022		\N	\N	2025-11-27 12:29:16.546919	\N	\N	\N	\N	\N	skipped_by_user
370	-4833184749	7392840491	89	reagent	1259	1	18.62	18.15	2025-11-27 21:25:16.057287		44.019173	58.722722	2025-11-27 16:25:29.322181	\N	\N	\N	\N	\N	received
371	-4833184749	7392840491	43	reagent	SW-OF	1	17.56	16.38	2025-11-27 21:44:36.952174		44.050193	58.699702	2025-11-27 16:44:50.790418	\N	\N	\N	\N	\N	received
372	-4833184749	7392840491	87	pressure	\N	\N	17.66	16.46	2025-11-27 21:54:54.243508		44.059866	58.696346	2025-11-27 16:55:07.710186	\N	\N	\N	\N	\N	received
373	-4833184749	7392840491	48	reagent	SW-OF	1	17.43	16.53	2025-11-27 22:06:04.928898		44.058499	58.686828	2025-11-27 17:06:17.646811	\N	\N	\N	\N	\N	received
374	-4833184749	7392840491	61	pressure	\N	\N	18.38	17.01	2025-11-27 23:07:19.571492		\N	\N	2025-11-27 18:07:42.076383	\N	\N	\N	\N	\N	timeout
375	-4833184749	7392840491	89	reagent	1259	1	18.46	18.07	2025-11-28 09:46:05.963057		44.019087	58.723021	2025-11-28 04:46:20.209158	\N	\N	\N	\N	\N	received
376	-4833184749	1042194608	43	pressure	\N	\N	64.34	15.97	2025-11-28 10:03:48.342529		44.050099	58.699858	2025-11-28 05:03:59.659905	\N	\N	\N	\N	\N	received
377	-4833184749	1042194608	43	pressure	\N	\N	64.4	15.97	2025-11-28 10:04:55.492248	Штуцер заморожен	44.050106	58.699626	2025-11-28 05:05:56.640693	\N	\N	\N	\N	\N	received
378	-4833184749	6730772526	43	other	\N	\N	\N	\N	2025-11-28 10:09:26.308446	Гидраты штуцера в связи с понижение наружного температура на минусовую, на 43скв отсутствует метанольницы [Давления ДО: Труб.=64.4 атм; Лин.=16.0 атм | ПОСЛЕ: Труб.=64.4 атм; Лин.=15.9 атм]	\N	\N	2025-11-28 05:12:20.326347	\N	\N	\N	\N	\N	skipped_by_user
379	-4833184749	6730772526	43	pressure	\N	\N	24.85	16.56	2025-11-28 10:14:06.482119	Замер давления после продувки штуцера	\N	\N	2025-11-28 05:14:27.366298	\N	\N	\N	\N	\N	skipped_by_user
380	-4833184749	1042194608	87	pressure	\N	\N	17.53	16.44	2025-11-28 10:24:24.06794		44.060054	58.696527	2025-11-28 05:24:36.154782	\N	\N	\N	\N	\N	received
381	-4833184749	7392840491	48	reagent	1253	1	17.46	16.54	2025-11-28 10:28:49.114712		44.058498	58.68664	2025-11-28 05:29:01.094666	\N	\N	\N	\N	\N	received
382	-4833184749	1042194608	61	pressure	\N	\N	17.97	16.52	2025-11-28 10:41:15.556933		44.075876	58.663883	2025-11-28 05:41:27.051152	\N	\N	\N	\N	\N	received
383	-4833184749	7392840491	43	reagent	SW-OF	1	17.06	16.61	2025-11-28 11:29:22.427104		44.048526	58.699793	2025-11-28 06:29:35.114556	\N	\N	\N	\N	\N	received
384	-4833184749	7392840491	43	reagent	Liquid Foam	1	16.21	15.74	2025-11-28 14:09:07.535583		44.050007	58.699709	2025-11-28 09:09:20.992945	\N	\N	\N	\N	\N	received
385	-4833184749	7392840491	48	reagent	1259	1	16.68	15.97	2025-11-28 14:19:44.038512		44.058391	58.686768	2025-11-28 09:19:53.869386	\N	\N	\N	\N	\N	received
386	-4833184749	6730772526	89	other	\N	\N	\N	\N	2025-11-28 14:43:27.589644	Перезагрузка манометра	\N	\N	2025-11-28 10:29:36.882232	\N	\N	\N	\N	\N	skipped_by_user
387	-4833184749	7392840491	89	reagent	1259	1	18.23	17.82	2025-11-28 21:21:15.491826		44.019034	58.722839	2025-11-28 16:21:27.992688	\N	\N	\N	\N	\N	received
388	-4833184749	7392840491	43	reagent	SW-OF	1	17.06	16.39	2025-11-28 21:40:29.281708		44.050126	58.699609	2025-11-28 16:40:56.831358	\N	\N	\N	\N	\N	received
389	-4833184749	1042194608	87	reagent	1253	1	17.64	16.5	2025-11-28 21:54:27.83765		44.059918	58.69634	2025-11-28 16:54:39.202615	\N	\N	\N	\N	\N	received
390	-4833184749	7392840491	48	reagent	Oil Foam	1	17.48	16.59	2025-11-28 21:58:30.690272		44.0584	58.686912	2025-11-28 16:58:44.673297	\N	\N	\N	\N	\N	received
392	-4833184749	7392840491	43	other	\N	\N	\N	\N	2025-11-29 08:54:36.873128	Перезагрузка манометра	44.049423	58.699676	2025-11-29 03:55:20.866736	\N	\N	\N	\N	\N	received
393	-4833184749	7392840491	43	reagent	SW-OF	1	16.99	16.42	2025-11-29 08:56:46.502368		44.050193	58.699702	2025-11-29 03:57:04.601677	\N	\N	\N	\N	\N	received
394	-4833184749	7392840491	89	reagent	1259	1	18.51	18.18	2025-11-29 09:14:07.414693		44.019078	58.722877	2025-11-29 04:14:22.528397	\N	\N	\N	\N	\N	received
395	-4833184749	7392840491	87	reagent	1259	1	17.53	16.4	2025-11-29 09:33:51.922665		44.059954	58.696422	2025-11-29 04:34:05.99466	\N	\N	\N	\N	\N	received
396	-4833184749	7392840491	48	reagent	Oil Foam	1	17.04	16.38	2025-11-29 09:39:32.055661		44.058265	58.686913	2025-11-29 04:39:45.750043	\N	\N	\N	\N	\N	received
397	-4833184749	1042194608	61	pressure	\N	\N	17.88	16.7	2025-11-29 09:57:47.983481		\N	\N	2025-11-29 04:57:58.062825	\N	\N	\N	\N	\N	skipped_by_user
398	-4833184749	1042194608	89	reagent	1259	1	19.16	19.02	2025-11-29 16:41:29.39041		44.019134	58.722943	2025-11-29 11:41:39.564159	\N	\N	\N	\N	\N	received
399	-4833184749	1042194608	89	pressure	\N	\N	20.52	19.73	2025-11-29 17:59:16.338284		\N	\N	2025-11-29 12:59:28.133411	\N	\N	\N	\N	\N	skipped_by_user
400	-4833184749	1042194608	87	pressure	\N	\N	18.76	16.37	2025-11-29 17:59:53.947981		\N	\N	2025-11-29 13:00:02.32621	\N	\N	\N	\N	\N	skipped_by_user
401	-4833184749	1042194608	48	pressure	\N	\N	17.37	16.54	2025-11-29 18:00:26.590568		\N	\N	2025-11-29 13:00:34.792841	\N	\N	\N	\N	\N	skipped_by_user
402	-4833184749	1042194608	43	pressure	\N	\N	17.23	16.33	2025-11-29 18:00:53.781083		\N	\N	2025-11-29 13:01:04.938684	\N	\N	\N	\N	\N	skipped_by_user
403	-4833184749	1042194608	61	pressure	\N	\N	17.84	16.51	2025-11-29 18:01:49.269852		\N	\N	2025-11-29 13:01:59.821154	\N	\N	\N	\N	\N	skipped_by_user
404	-4833184749	7392840491	89	reagent	1259	1	20.29	19.84	2025-11-29 21:13:32.699301	.	44.019256	58.722727	2025-11-29 16:13:47.579655	\N	\N	\N	\N	\N	received
405	-4833184749	7392840491	43	reagent	SW-OF	1	17.33	16.47	2025-11-29 21:34:12.711489	.	44.050169	58.699647	2025-11-29 16:34:25.673593	\N	\N	\N	\N	\N	received
406	-4833184749	1042194608	87	pressure	\N	\N	18.72	16.5	2025-11-29 21:42:11.905836		44.059939	58.696511	2025-11-29 16:42:25.88997	\N	\N	\N	\N	\N	received
407	-4833184749	7392840491	48	reagent	Oil Foam	1	17.38	16.6	2025-11-29 21:48:01.056764	.	44.058455	58.68679	2025-11-29 16:48:13.494637	\N	\N	\N	\N	\N	received
408	-4833184749	7392840491	61	pressure	\N	\N	18.05	16.77	2025-11-29 22:03:01.593487	.	\N	\N	2025-11-29 17:03:17.258809	\N	\N	\N	\N	\N	skipped_by_user
409	-4833184749	7392840491	89	reagent	1259	1	17.71	17.14	2025-11-30 09:24:35.569502	.	44.01909	58.722905	2025-11-30 04:24:51.31441	\N	\N	\N	\N	\N	received
410	-4833184749	7392840491	43	reagent	SW-OF	1	17.7	16.36	2025-11-30 09:47:14.439445	.	44.05005	58.69956	2025-11-30 04:47:29.090285	\N	\N	\N	\N	\N	received
411	-4833184749	1042194608	87	pressure	\N	\N	18.81	16.55	2025-11-30 09:56:09.205709		44.059878	58.696373	2025-11-30 04:56:20.031195	\N	\N	\N	\N	\N	received
412	-4833184749	7392840491	48	reagent	SW-OF	1	16.76	16.55	2025-11-30 10:03:16.22051	.	44.058415	58.686823	2025-11-30 05:03:32.569868	\N	\N	\N	\N	\N	received
413	-4833184749	1042194608	61	pressure	\N	\N	18.29	17.23	2025-11-30 10:38:48.71777		\N	\N	2025-11-30 05:38:57.096405	\N	\N	\N	\N	\N	skipped_by_user
414	-4833184749	7392840491	48	reagent	1253	1	17.22	16.72	2025-11-30 14:47:12.734738	.	44.058503	58.6869	2025-11-30 09:47:24.898643	\N	\N	\N	\N	\N	received
415	-4833184749	1042194608	89	pressure	\N	\N	17.07	16.54	2025-11-30 14:59:45.394533		\N	\N	2025-11-30 09:59:53.489708	\N	\N	\N	\N	\N	skipped_by_user
416	-4833184749	1042194608	87	pressure	\N	\N	19.19	16.86	2025-11-30 15:00:21.639279		\N	\N	2025-11-30 10:00:29.145097	\N	\N	\N	\N	\N	skipped_by_user
417	-4833184749	1042194608	43	pressure	\N	\N	18.18	16.8	2025-11-30 15:00:45.276458		\N	\N	2025-11-30 10:00:53.722091	\N	\N	\N	\N	\N	skipped_by_user
418	-4833184749	1042194608	61	pressure	\N	\N	18.64	17.58	2025-11-30 15:01:22.925674		\N	\N	2025-11-30 10:01:44.84051	\N	\N	\N	\N	\N	skipped_by_user
419	-4833184749	1042194608	89	reagent	Super Foam	1	17.63	17.29	2025-11-30 17:35:39.301522		44.019245	58.722887	2025-11-30 12:45:58.089883	\N	\N	\N	\N	\N	received
420	-4833184749	7392840491	89	reagent	1259	1	18.86	18.41	2025-11-30 21:39:26.358801	.	44.0192	58.720552	2025-11-30 16:39:42.307547	\N	\N	\N	\N	\N	received
421	-4833184749	7392840491	43	reagent	SW-OF	1	18.12	16.71	2025-11-30 21:59:39.212897		44.05009	58.699714	2025-11-30 16:59:51.986198	\N	\N	\N	\N	\N	received
422	-4833184749	7392840491	87	pressure	\N	\N	19.09	16.66	2025-11-30 22:22:32.06448		44.05009	58.699714	2025-11-30 17:22:46.848092	\N	\N	\N	\N	\N	received
423	-4833184749	7392840491	61	pressure	\N	\N	18.24	17.16	2025-11-30 22:23:38.859565		\N	\N	2025-11-30 17:23:48.118072	\N	\N	\N	\N	\N	skipped_by_user
424	-4833184749	1042194608	48	reagent	Oil Foam	2	17.12	16.91	2025-11-30 23:38:09.640065		44.058415	58.686823	2025-11-30 18:38:20.958871	\N	\N	\N	\N	\N	received
425	-4833184749	7392840491	89	reagent	1259	1	19.42	18.77	2025-12-01 09:12:24.48021	‐	44.019102	58.722932	2025-12-01 04:12:48.135889	\N	\N	\N	\N	\N	received
426	-4833184749	7392840491	43	reagent	SW-OF	1	17.85	16.61	2025-12-01 09:28:50.483133	‐	44.050217	58.699757	2025-12-01 04:29:04.173003	\N	\N	\N	\N	\N	received
427	-4833184749	1042194608	87	pressure	\N	\N	18.99	16.66	2025-12-01 09:41:21.974013		44.059951	58.696538	2025-12-01 04:41:32.76663	\N	\N	\N	\N	\N	received
428	-4833184749	1042194608	48	purge	\N	\N	17.09	16.65	2025-12-01 09:58:01.115495		44.058388	58.686884	2025-12-01 04:58:48.565563	\N	\N	\N	start	\N	received
429	-4833184749	1042194608	48	purge	\N	\N	1.79	16.15	2025-12-01 10:11:41.653		44.058423	58.686779	2025-12-01 05:12:13.572589	\N	\N	\N	press	\N	received
430	-4833184749	1042194608	48	purge	\N	\N	23.44	16.08	2025-12-01 10:21:39.005703		44.058543	58.686867	2025-12-01 05:22:05.080254	\N	\N	\N	stop	\N	received
431	-4833184749	7392840491	61	pressure	\N	\N	18.01	16.8	2025-12-01 10:39:06.095881	‐	44.075927	58.663689	2025-12-01 05:39:19.959223	\N	\N	\N	\N	\N	received
432	-4833184749	1042194608	89	reagent	1259	1	20.15	20.11	2025-12-01 17:03:52.863738		44.01907	58.722922	2025-12-01 12:04:02.99589	\N	\N	\N	\N	\N	received
433	-4833184749	7392840491	89	pressure	\N	\N	19.05	18.36	2025-12-01 21:34:33.599852	‐	\N	\N	2025-12-01 16:34:44.199055	\N	\N	\N	\N	\N	skipped_by_user
434	-4833184749	7392840491	43	pressure	\N	\N	18.12	16.6	2025-12-01 21:35:22.452946	‐	\N	\N	2025-12-01 16:35:36.093841	\N	\N	\N	\N	\N	skipped_by_user
435	-4833184749	7392840491	87	pressure	\N	\N	18.63	16.65	2025-12-01 21:36:08.238256	‐	\N	\N	2025-12-01 16:36:18.040004	\N	\N	\N	\N	\N	skipped_by_user
436	-4833184749	7392840491	48	pressure	\N	\N	17.44	16.83	2025-12-01 21:37:04.936224	‐	\N	\N	2025-12-01 16:37:14.837018	\N	\N	\N	\N	\N	skipped_by_user
437	-4833184749	7392840491	61	pressure	\N	\N	17.85	16.76	2025-12-01 21:37:47.830678	‐	\N	\N	2025-12-01 16:37:57.794655	\N	\N	\N	\N	\N	skipped_by_user
438	-4833184749	7392840491	89	reagent	1259	1	18.98	18.32	2025-12-01 21:56:29.878721	‐	44.019149	58.722855	2025-12-01 16:56:42.369871	\N	\N	\N	\N	\N	received
439	-4833184749	7392840491	48	reagent	Oil Foam	1	17.53	16.88	2025-12-01 22:29:45.594076	‐	44.058455	58.68679	2025-12-01 17:30:01.831606	\N	\N	\N	\N	\N	received
440	-4833184749	7392840491	89	pressure	\N	\N	17.4	16.79	2025-12-02 08:42:39.005936	‐	\N	\N	2025-12-02 03:42:48.83241	\N	\N	\N	\N	\N	skipped_by_user
441	-4833184749	7392840491	87	pressure	\N	\N	18.7	16.96	2025-12-02 08:43:28.986722	‐	\N	\N	2025-12-02 03:43:38.02867	\N	\N	\N	\N	\N	skipped_by_user
442	-4833184749	7392840491	48	pressure	\N	\N	17.71	17.14	2025-12-02 08:44:16.766725	‐	\N	\N	2025-12-02 03:44:25.589588	\N	\N	\N	\N	\N	skipped_by_user
443	-4833184749	7392840491	43	pressure	\N	\N	21.21	16.93	2025-12-02 08:45:01.932027	‐	\N	\N	2025-12-02 03:45:11.193864	\N	\N	\N	\N	\N	skipped_by_user
444	-4833184749	7392840491	61	pressure	\N	\N	18.63	17.14	2025-12-02 08:46:00.825096	‐	\N	\N	2025-12-02 03:46:10.474025	\N	\N	\N	\N	\N	skipped_by_user
445	-4833184749	7392840491	89	reagent	1259	1	17.56	17.05	2025-12-02 09:06:58.019156	‐	44.018954	58.722718	2025-12-02 04:07:15.045063	\N	\N	\N	\N	\N	received
446	-4833184749	1042194608	43	pressure	\N	\N	21.16	16.8	2025-12-02 09:27:24.089383	Замер давления после продувки штуцера	44.049913	58.699185	2025-12-02 04:27:54.777672	\N	\N	\N	\N	\N	received
447	-4833184749	1042194608	43	purge	\N	\N	21.16	16.8	2025-12-02 09:29:01.906689	Продувка штуцера	44.049971	58.699815	2025-12-02 04:29:32.119793	\N	\N	\N	start	\N	received
448	-4833184749	1042194608	43	purge	\N	\N	19.84	16.9	2025-12-02 09:35:57.118342		\N	\N	2025-12-02 04:36:25.36835	\N	\N	\N	stop	\N	skipped_by_user
449	-4833184749	1042194608	43	pressure	\N	\N	21.16	16.8	2025-12-02 09:29:01.258689	Замер давления до продувки штуцера	44.050183	58.700051	2025-12-02 04:42:38.266625	\N	\N	\N	\N	\N	received
450	-4833184749	1042194608	43	other	\N	\N	\N	\N	2025-12-02 09:29:19.451885	[Давления ДО: Труб.=21.2 атм; Лин.=16.8 атм | ПОСЛЕ: Труб.=19.8 атм; Лин.=16.9 атм]	44.050269	58.699752	2025-12-02 04:46:17.194001	\N	\N	\N	\N	\N	received
451	-4833184749	1042194608	43	reagent	SW-OF	1	17.68	16.83	2025-12-02 09:49:49.487479		44.050273	58.699823	2025-12-02 04:49:58.27009	\N	\N	\N	\N	\N	received
452	-4833184749	1042194608	48	reagent	Oil Foam	1	17.61	16.92	2025-12-02 10:10:27.356063		\N	\N	2025-12-02 05:10:35.681362	\N	\N	\N	\N	\N	skipped_by_user
453	-4833184749	7392840491	89	pressure	\N	\N	19.67	19.22	2025-12-02 13:48:58.816592	‐	44.058371	58.686785	2025-12-02 08:49:14.888358	\N	\N	\N	\N	\N	received
454	-4833184749	7392840491	43	reagent	SW-OF	1	16.85	16.36	2025-12-02 14:04:33.763367	‐	44.050202	58.699846	2025-12-02 09:04:53.615929	\N	\N	\N	\N	\N	received
455	-4833184749	1042194608	87	pressure	\N	\N	18.35	16.38	2025-12-02 14:11:37.565896		44.05993	58.696367	2025-12-02 09:11:48.832111	\N	\N	\N	\N	\N	received
456	-4833184749	1042194608	87	pressure	\N	\N	18.35	16.38	2025-12-02 14:11:43.732658		44.05993	58.696367	2025-12-02 09:15:09.107478	\N	\N	\N	\N	\N	received
457	-4833184749	1042194608	48	pressure	\N	\N	17.12	16.42	2025-12-02 14:17:54.515871		44.058324	58.686863	2025-12-02 09:18:04.509199	\N	\N	\N	\N	\N	received
458	-4833184749	1042194608	61	pressure	\N	\N	18.42	17.09	2025-12-02 15:06:22.611001		44.075876	58.663883	2025-12-02 10:06:36.635199	\N	\N	\N	\N	\N	received
459	-4833184749	1042194608	89	reagent	1251	1	21.86	21.83	2025-12-02 16:43:59.694621		44.01911	58.722888	2025-12-02 11:44:09.301386	\N	\N	\N	\N	\N	received
460	-4833184749	7392840491	89	reagent	1251	1	18.52	17.97	2025-12-02 21:59:07.631922	‐	44.01907	58.722922	2025-12-02 16:59:23.14458	\N	\N	\N	\N	\N	received
461	-4833184749	7392840491	43	reagent	SW-OF	1	16.88	16.45	2025-12-02 22:14:42.740534	‐	44.05015	58.699664	2025-12-02 17:14:59.787798	\N	\N	\N	\N	\N	received
462	-4833184749	1042194608	87	pressure	\N	\N	18.44	16.51	2025-12-02 22:23:56.653858		44.059934	58.696439	2025-12-02 17:24:10.21889	\N	\N	\N	\N	\N	received
463	-4833184749	1042194608	48	reagent	Oil Foam	1	17.18	16.55	2025-12-02 22:29:25.889411		44.058522	58.686695	2025-12-02 17:29:38.997671	\N	\N	\N	\N	\N	received
464	-4833184749	7392840491	61	pressure	\N	\N	18.22	17.16	2025-12-02 22:47:17.814732	‐	\N	\N	2025-12-02 17:47:45.694877	\N	\N	\N	\N	\N	timeout
465	-4833184749	1042194608	89	pressure	\N	\N	18.51	18.07	2025-12-03 08:49:36.310114		\N	\N	2025-12-03 03:49:44.269673	\N	\N	\N	\N	\N	skipped_by_user
466	-4833184749	1042194608	87	pressure	\N	\N	17.8	16.12	2025-12-03 08:50:09.91095		\N	\N	2025-12-03 03:50:16.888537	\N	\N	\N	\N	\N	skipped_by_user
467	-4833184749	1042194608	48	pressure	\N	\N	16.83	16.55	2025-12-03 08:50:42.000208		\N	\N	2025-12-03 03:51:02.204409	\N	\N	\N	\N	\N	skipped_by_user
468	-4833184749	1042194608	43	pressure	\N	\N	16.88	16.21	2025-12-03 08:51:22.480379		\N	\N	2025-12-03 03:51:32.946773	\N	\N	\N	\N	\N	skipped_by_user
469	-4833184749	1042194608	61	pressure	\N	\N	17.34	15.99	2025-12-03 08:52:15.111383		\N	\N	2025-12-03 03:52:24.476238	\N	\N	\N	\N	\N	skipped_by_user
470	-4833184749	7392840491	43	reagent	SW-OF	1	16.57	16.18	2025-12-03 09:12:40.445693	Трубной манометр перезагрузка до 16.88 после 16.57	44.050174	58.699907	2025-12-03 04:15:17.481701	\N	\N	\N	\N	\N	received
471	-4833184749	7392840491	89	reagent	1251	1	18.36	18	2025-12-03 09:31:07.305136	‐	44.019405	58.723129	2025-12-03 04:31:23.146952	\N	\N	\N	\N	\N	received
472	-4833184749	7392840491	48	reagent	Sand Stick	1	16.82	16.59	2025-12-03 10:09:07.947162	‐	44.058486	58.686613	2025-12-03 05:09:21.710479	\N	\N	\N	\N	\N	received
473	-4833184749	1042194608	89	pressure	\N	\N	19.09	18.46	2025-12-03 13:49:09.064483		44.01907	58.722922	2025-12-03 08:49:23.652187	\N	\N	\N	\N	\N	received
474	-4833184749	1042194608	43	pressure	\N	\N	19.07	18.45	2025-12-03 14:07:01.171215		44.050273	58.699823	2025-12-03 09:07:13.89659	\N	\N	\N	\N	\N	received
475	-4833184749	1042194608	87	pressure	\N	\N	17.99	16.43	2025-12-03 14:15:37.141539		44.05993	58.696367	2025-12-03 09:15:51.194359	\N	\N	\N	\N	\N	received
476	-4833184749	1042194608	48	pressure	\N	\N	17.01	16.47	2025-12-03 14:23:27.490679		44.058522	58.686695	2025-12-03 09:23:44.297837	\N	\N	\N	\N	\N	received
477	-4833184749	1042194608	61	pressure	\N	\N	17.86	16.76	2025-12-03 14:51:37.414867		44.075876	58.663883	2025-12-03 09:51:55.48948	\N	\N	\N	\N	\N	received
478	-4833184749	1042194608	89	reagent	1251	1	18.28	18.06	2025-12-03 16:18:13.977173		44.019134	58.722943	2025-12-03 11:18:24.1313	\N	\N	\N	\N	\N	received
479	-4833184749	7392840491	89	reagent	1251	1	19.73	19.24	2025-12-03 20:19:20.125788	‐	44.019122	58.722916	2025-12-03 15:19:33.66887	\N	\N	\N	\N	\N	received
480	-4833184749	7392840491	43	reagent	SW-OF	2	16.86	16.66	2025-12-03 20:44:13.859462	Трубной манометр мерезагрузка до 16.93 после 16.85	44.050007	58.699709	2025-12-03 15:46:17.779428	\N	\N	\N	\N	\N	received
481	-4833184749	1042194608	87	pressure	\N	\N	18.08	16.66	2025-12-03 20:56:14.935793	'	44.059768	58.696429	2025-12-03 15:56:29.811182	\N	\N	\N	\N	\N	received
482	-4833184749	1042194608	48	reagent	Oil Foam	1	17.5	16.85	2025-12-03 21:02:02.158298		44.058467	58.686817	2025-12-03 16:02:12.974075	\N	\N	\N	\N	\N	received
483	-4833184749	1042194608	61	pressure	\N	\N	18.81	17.24	2025-12-03 21:29:39.61167		44.075876	58.663883	2025-12-03 16:29:56.876141	\N	\N	\N	\N	\N	received
484	-4833184749	1042194608	89	pressure	\N	\N	19.22	18.86	2025-12-04 08:55:45.164571		\N	\N	2025-12-04 03:56:05.657654	\N	\N	\N	\N	\N	skipped_by_user
485	-4833184749	1042194608	87	pressure	\N	\N	17.85	16.55	2025-12-04 08:56:43.110629	'	\N	\N	2025-12-04 03:56:52.66798	\N	\N	\N	\N	\N	skipped_by_user
486	-4833184749	1042194608	48	pressure	\N	\N	16.68	16.53	2025-12-04 08:57:13.318327		\N	\N	2025-12-04 03:57:23.760715	\N	\N	\N	\N	\N	skipped_by_user
487	-4833184749	1042194608	43	pressure	\N	\N	17.02	16.37	2025-12-04 08:57:41.956437		\N	\N	2025-12-04 03:57:53.558157	\N	\N	\N	\N	\N	skipped_by_user
488	-4833184749	1042194608	61	pressure	\N	\N	17.94	17.02	2025-12-04 08:58:13.94289		\N	\N	2025-12-04 03:58:22.633793	\N	\N	\N	\N	\N	skipped_by_user
489	-4833184749	1042194608	89	reagent	1251	1	19.15	18.82	2025-12-04 09:13:45.547509		44.019125	58.7228	2025-12-04 04:13:55.75992	\N	\N	\N	\N	\N	received
490	-4833184749	1042194608	43	reagent	SW-OF	1	17.01	16.41	2025-12-04 09:31:19.238946		44.050074	58.699615	2025-12-04 04:31:31.65301	\N	\N	\N	\N	\N	received
491	-4833184749	1042194608	48	reagent	Oil Foam	1	16.48	16.34	2025-12-04 09:49:06.325534		44.058335	58.686703	2025-12-04 04:49:15.666929	\N	\N	\N	\N	\N	received
492	-4833184749	1042194608	48	purge	\N	\N	16.84	16.6	2025-12-04 14:46:36.452339		44.058391	58.686768	2025-12-04 09:47:05.487806	\N	\N	\N	start	\N	received
493	-4833184749	1042194608	48	purge	\N	\N	2.49	16.38	2025-12-04 15:46:11.150808		44.058391	58.686768	2025-12-04 10:47:14.843182	\N	\N	\N	press	\N	received
494	-4833184749	1042194608	48	purge	\N	\N	23.7	16.36	2025-12-04 16:00:35.355852		44.058446	58.686646	2025-12-04 11:00:55.316241	\N	\N	\N	stop	\N	received
495	-4833184749	7392840491	89	reagent	1259	1	18.37	18.07	2025-12-04 16:48:52.579444	‐	44.026282	58.716113	2025-12-04 11:49:08.577913	\N	\N	\N	\N	\N	received
496	-4833184749	7392840491	89	reagent	1259	1	18.83	18.41	2025-12-04 21:16:08.2591	‐	44.019086	58.722833	2025-12-04 16:16:22.573781	\N	\N	\N	\N	\N	received
497	-4833184749	7392840491	43	reagent	SW-OF	1	18.13	16.19	2025-12-04 21:34:32.051355	‐	44.050011	58.699781	2025-12-04 16:34:47.293357	\N	\N	\N	\N	\N	received
498	-4833184749	1042194608	87	pressure	\N	\N	17.68	16.49	2025-12-04 21:42:27.653696		44.059966	58.69645	2025-12-04 16:42:40.444295	\N	\N	\N	\N	\N	received
499	-4833184749	1042194608	48	reagent	Oil Foam	1	17.24	16.5	2025-12-04 21:48:08.670798		44.058323	58.686675	2025-12-04 16:48:21.44377	\N	\N	\N	\N	\N	received
500	-4833184749	1042194608	61	pressure	\N	\N	17.89	16.91	2025-12-04 22:08:36.590839		44.075876	58.663883	2025-12-04 17:08:52.241588	\N	\N	\N	\N	\N	received
501	-4833184749	1042194608	89	pressure	\N	\N	20.07	19.71	2025-12-05 08:54:57.348781		\N	\N	2025-12-05 03:55:05.581267	\N	\N	\N	\N	\N	skipped_by_user
502	-4833184749	1042194608	87	pressure	\N	\N	17.26	16.17	2025-12-05 08:55:25.663093		\N	\N	2025-12-05 03:55:40.636387	\N	\N	\N	\N	\N	skipped_by_user
503	-4833184749	1042194608	48	pressure	\N	\N	16.87	16.23	2025-12-05 08:56:02.647071		\N	\N	2025-12-05 03:56:10.267731	\N	\N	\N	\N	\N	skipped_by_user
504	-4833184749	1042194608	43	pressure	\N	\N	18.45	16.27	2025-12-05 08:56:48.319022		\N	\N	2025-12-05 03:57:01.534592	\N	\N	\N	\N	\N	skipped_by_user
505	-4833184749	1042194608	61	pressure	\N	\N	17.05	16.47	2025-12-05 08:57:19.209638		\N	\N	2025-12-05 03:57:25.945173	\N	\N	\N	\N	\N	skipped_by_user
506	-4833184749	7392840491	89	reagent	1259	1	19.84	19.51	2025-12-05 09:19:04.587475	‐	44.019197	58.722777	2025-12-05 04:19:29.493775	\N	\N	\N	\N	\N	received
507	-4833184749	7392840491	43	reagent	SW-OF	1	18.41	16.22	2025-12-05 09:37:54.566306	‐	44.05023	58.699785	2025-12-05 04:38:07.4182	\N	\N	\N	\N	\N	received
508	-4833184749	1042194608	48	reagent	SW-OF	1	16.81	16.12	2025-12-05 09:53:13.941717		44.058367	58.686713	2025-12-05 04:53:22.339069	\N	\N	\N	\N	\N	received
509	-4833184749	1042194608	61	reagent	Oil Foam	1	17.09	16.39	2025-12-05 10:04:31.839108		44.07592	58.663921	2025-12-05 05:04:43.190528	\N	\N	\N	\N	\N	received
510	-4833184749	1042194608	61	other	\N	\N	\N	\N	2025-12-05 10:54:52.602422	[Давления ДО: Труб.=13.0 атм; Лин.=16.8 атм | ПОСЛЕ: Труб.=18.3 атм; Лин.=16.8 атм]	44.076042	58.663704	2025-12-05 06:00:13.823185	\N	\N	\N	\N	\N	received
511	-4833184749	1042194608	48	reagent	Sand Stick	1	16.78	16.08	2025-12-05 15:09:59.644885		44.058411	58.686752	2025-12-05 10:10:11.653569	\N	\N	\N	\N	\N	received
512	-4833184749	7392840491	89	reagent	1259	1	19.25	18.99	2025-12-05 15:57:01.450882	‐	44.019106	58.722816	2025-12-05 10:57:18.822287	\N	\N	\N	\N	\N	received
513	-4833184749	7392840491	120	other	\N	\N	\N	\N	2025-12-05 16:44:16.167932	Осмотр скважины	\N	\N	2025-12-05 11:44:43.88191	\N	\N	\N	\N	\N	timeout
514	-4833184749	7392840491	120	other	\N	\N	\N	\N	2025-12-05 16:45:30.135686	Осмотр скважины	44.038795	58.692227	2025-12-05 11:45:47.823985	\N	\N	\N	\N	\N	received
515	-4833184749	7392840491	131	other	\N	\N	\N	\N	2025-12-05 16:50:39.828887	Осмотр скважины	44.036287	58.707693	2025-12-05 11:50:55.297802	\N	\N	\N	\N	\N	received
516	-4833184749	7392840491	134	other	\N	\N	\N	\N	2025-12-05 17:02:24.601642	Осмотр скважины	44.019549	58.731709	2025-12-05 12:02:38.713828	\N	\N	\N	\N	\N	received
517	-4833184749	7392840491	117	other	\N	\N	\N	\N	2025-12-05 17:04:28.972439	Осмотр скважины	44.016047	58.730636	2025-12-05 12:04:41.521762	\N	\N	\N	\N	\N	received
518	-4833184749	1042194608	85	other	\N	\N	\N	\N	2025-12-05 17:13:51.543265	Осмотр скважины	43.992208	58.719285	2025-12-05 12:14:28.071984	\N	\N	\N	\N	\N	received
519	-4833184749	1042194608	127	other	\N	\N	\N	\N	2025-12-05 17:16:12.415008	Осмотр скважины	43.990403	58.715464	2025-12-05 12:16:36.628798	\N	\N	\N	\N	\N	received
520	-4833184749	1042194608	129	other	\N	\N	\N	\N	2025-12-05 17:24:07.94273	Осмотр скважины	43.98784	58.720941	2025-12-05 12:24:40.8451	\N	\N	\N	\N	\N	received
521	-4833184749	1042194608	48	other	\N	\N	\N	\N	2025-12-05 17:51:28.336343	[Давления ДО: Труб.=15.8 атм; Лин.=15.8 атм | ПОСЛЕ: Труб.=17.0 атм; Лин.=15.8 атм]	44.058391	58.686768	2025-12-05 12:52:22.074963	\N	\N	\N	\N	\N	received
522	-4833184749	1042194608	48	reagent	Sand Stick	1	17.04	15.83	2025-12-05 17:53:15.7149		44.058523	58.686883	2025-12-05 12:53:28.54468	\N	\N	\N	\N	\N	received
523	-4833184749	7392840491	89	reagent	1259	1	20.14	19.7	2025-12-05 20:40:23.170959	‐	44.019034	58.722839	2025-12-05 15:40:36.556688	\N	\N	\N	\N	\N	received
524	-4833184749	7392840491	43	reagent	SW-OF	1	17.81	16.77	2025-12-05 20:59:18.10692	‐	44.050179	58.699979	2025-12-05 15:59:41.758014	\N	\N	\N	\N	\N	received
525	-4833184749	7392840491	87	reagent	1259	1	17.63	16.75	2025-12-05 21:20:13.800826	‐	44.059922	58.696412	2025-12-05 16:20:28.689774	\N	\N	\N	\N	\N	received
526	-4833184749	1042194608	48	other	\N	\N	\N	\N	2025-12-05 21:27:34.70836	[Давления ДО: Труб.=9.3 атм; Лин.=16.7 атм | ПОСЛЕ: Труб.=17.4 атм; Лин.=16.7 атм]	44.058582	58.686833	2025-12-05 16:32:59.224285	\N	\N	\N	\N	\N	received
527	-4833184749	1042194608	48	reagent	Sand Stick	1	17.4	16.7	2025-12-05 21:36:14.319502		44.058376	58.686857	2025-12-05 16:36:31.40997	\N	\N	\N	\N	\N	received
528	-4833184749	1042194608	61	pressure	\N	\N	18.48	17.11	2025-12-05 22:10:17.643373		44.075876	58.663883	2025-12-05 17:10:31.849961	\N	\N	\N	\N	\N	received
529	-4833184749	1042194608	61	other	\N	\N	\N	\N	2025-12-06 09:29:10.875795	[Давления ДО: Труб.=12.6 атм; Лин.=16.5 атм | ПОСЛЕ: Труб.=17.8 атм; Лин.=16.5 атм]	44.075927	58.663689	2025-12-06 04:37:00.355235	\N	\N	\N	\N	\N	received
530	-4833184749	7392840491	61	pressure	\N	\N	17.81	16.49	2025-12-06 09:38:04.904114	‐	44.075816	58.663745	2025-12-06 04:38:17.748023	\N	\N	\N	\N	\N	received
531	-4833184749	1042194608	48	other	\N	\N	\N	\N	2025-12-06 09:51:47.308686	[Давления ДО: Труб.=7.4 атм; Лин.=16.7 атм | ПОСЛЕ: Труб.=17.1 атм; Лин.=16.8 атм]	44.058383	58.686813	2025-12-06 04:58:59.498973	\N	\N	\N	\N	\N	received
532	-4833184749	1042194608	48	reagent	Oil Foam	1	17.13	16.8	2025-12-06 10:00:31.771169		44.058395	58.686652	2025-12-06 05:00:41.441597	\N	\N	\N	\N	\N	received
533	-4833184749	1042194608	87	pressure	\N	\N	18.79	16.91	2025-12-06 10:06:26.071393		44.059919	58.696528	2025-12-06 05:06:36.631542	\N	\N	\N	\N	\N	received
534	-4833184749	7392840491	43	reagent	SW-OF	1	17.55	16.45	2025-12-06 10:13:49.281764	‐	44.049983	58.699654	2025-12-06 05:14:03.915927	\N	\N	\N	\N	\N	received
535	-4833184749	7392840491	89	reagent	1259	1	20.32	19.83	2025-12-06 10:27:43.761066	‐	44.019253	58.722843	2025-12-06 05:27:56.795174	\N	\N	\N	\N	\N	received
536	-4833184749	1042194608	61	other	\N	\N	\N	\N	2025-12-06 12:02:34.224895	Гидраты под манометр [Давления ДО: Труб.=16.8 атм; Лин.=16.9 атм | ПОСЛЕ: Труб.=18.2 атм; Лин.=16.9 атм]	44.075849	58.663944	2025-12-06 07:06:30.466261	\N	\N	\N	\N	\N	received
537	-4833184749	7392840491	89	reagent	1259	1	20.1	19.62	2025-12-06 15:23:52.508063	‐	44.018887	58.722813	2025-12-06 10:24:06.163371	\N	\N	\N	\N	\N	received
538	-4833184749	1042194608	43	pressure	\N	\N	17.63	16.54	2025-12-06 15:45:49.96216		44.050273	58.699823	2025-12-06 10:46:09.598004	\N	\N	\N	\N	\N	received
539	-4833184749	1042194608	87	pressure	\N	\N	18.82	16.49	2025-12-06 15:59:39.999302		44.05993	58.696367	2025-12-06 11:00:03.960533	\N	\N	\N	\N	\N	received
540	-4833184749	1042194608	48	pressure	\N	\N	17.41	16.67	2025-12-06 16:06:27.555314		44.058522	58.686695	2025-12-06 11:06:40.852331	\N	\N	\N	\N	\N	received
541	-4833184749	1042194608	61	pressure	\N	\N	18.07	16.65	2025-12-06 16:21:45.881571		44.075876	58.663883	2025-12-06 11:22:00.706598	\N	\N	\N	\N	\N	received
542	-4833184749	7392840491	89	reagent	1259	1	17.94	17	2025-12-06 21:01:30.081274	‐	44.019161	58.722695	2025-12-06 16:01:48.533251	\N	\N	\N	\N	\N	received
543	-4833184749	7392840491	43	reagent	SW-OF	1	17.5	16.58	2025-12-06 21:19:32.220143	‐	44.050162	58.699692	2025-12-06 16:19:45.400713	\N	\N	\N	\N	\N	received
544	-4833184749	1042194608	87	pressure	\N	\N	18.51	16.54	2025-12-06 21:29:15.578608		44.059545	58.696354	2025-12-06 16:29:25.106204	\N	\N	\N	\N	\N	received
545	-4833184749	1042194608	48	reagent	Oil Foam	1	16.87	16.42	2025-12-06 21:35:20.482532		44.05847	58.686701	2025-12-06 16:35:31.639485	\N	\N	\N	\N	\N	received
546	-4833184749	7392840491	61	pressure	\N	\N	18.2	16.87	2025-12-06 21:50:59.271664	‐	\N	\N	2025-12-06 16:51:24.758598	\N	\N	\N	\N	\N	timeout
547	-4833184749	7392840491	89	pressure	\N	\N	16.93	16.49	2025-12-07 08:40:36.454206	‐	44.037796	58.686286	2025-12-07 03:40:51.757925	\N	\N	\N	\N	\N	received
548	-4833184749	7392840491	87	pressure	\N	\N	18.71	16.53	2025-12-07 08:41:47.993896	‐	44.037796	58.686286	2025-12-07 03:42:08.11167	\N	\N	\N	\N	\N	received
549	-4833184749	7392840491	48	pressure	\N	\N	16.67	16.39	2025-12-07 08:42:43.546948	‐	\N	\N	2025-12-07 03:43:03.156168	\N	\N	\N	\N	\N	timeout
550	-4833184749	7392840491	43	pressure	\N	\N	17.33	16.56	2025-12-07 08:43:48.374746	‐	\N	\N	2025-12-07 03:43:57.652957	\N	\N	\N	\N	\N	skipped_by_user
551	-4833184749	7392840491	61	pressure	\N	\N	18.05	16.79	2025-12-07 08:44:37.003759	‐	\N	\N	2025-12-07 03:44:47.727856	\N	\N	\N	\N	\N	skipped_by_user
552	-4833184749	7392840491	89	reagent	1259	1	16.98	16.57	2025-12-07 09:05:25.275935	‐	44.019113	58.722772	2025-12-07 04:05:36.731852	\N	\N	\N	\N	\N	received
553	-4833184749	7392840491	43	reagent	SW-OF	1	17.26	17.34	2025-12-07 09:24:10.637658	‐	44.050265	58.69968	2025-12-07 04:24:23.872652	\N	\N	\N	\N	\N	received
554	-4833184749	7392840491	48	reagent	SW-OF	1	16.58	16.29	2025-12-07 09:34:23.313651	‐	44.058395	58.68684	2025-12-07 04:34:37.298595	\N	\N	\N	\N	\N	received
555	-4833184749	7392840491	43	other	\N	\N	\N	\N	2025-12-07 10:35:06.064587	‐ [Давления ДО: Труб.=16.6 атм; Лин.=17.4 атм | ПОСЛЕ: Труб.=19.5 атм; Лин.=17.4 атм]	44.050135	58.69994	2025-12-07 06:01:35.548965	\N	\N	\N	\N	\N	received
556	-4833184749	6730772526	43	reagent	1251	1	19.5	17.4	2025-12-07 11:03:57.421491	Вброс после продувки штуцера	\N	\N	2025-12-07 08:26:03.96516	\N	\N	\N	\N	\N	skipped_by_user
557	-4833184749	7392840491	48	reagent	1251	1	17.02	16.77	2025-12-07 13:50:39.056985	‐	44.058479	58.686845	2025-12-07 08:50:53.061575	\N	\N	\N	\N	\N	received
558	-4833184749	7392840491	48	reagent	1253	1	17.06	16.79	2025-12-07 14:13:40.494354	‐	44.058364	58.686829	2025-12-07 09:13:53.03663	\N	\N	\N	\N	\N	received
559	-4833184749	7392840491	89	pressure	\N	\N	17.41	16.81	2025-12-07 14:31:17.539894	‐	44.01907	58.722922	2025-12-07 09:31:43.878151	\N	\N	\N	\N	\N	received
560	-4833184749	7392840491	43	pressure	\N	\N	20.78	16.9	2025-12-07 14:45:32.654037	‐	44.050273	58.699823	2025-12-07 09:45:52.364899	\N	\N	\N	\N	\N	received
561	-4833184749	7392840491	87	pressure	\N	\N	19.16	16.97	2025-12-07 14:54:48.150691	‐	\N	\N	2025-12-07 09:55:09.652722	\N	\N	\N	\N	\N	timeout
562	-4833184749	7392840491	48	pressure	\N	\N	17.35	16.93	2025-12-07 14:59:08.754768	‐	44.058522	58.686695	2025-12-07 09:59:25.97828	\N	\N	\N	\N	\N	received
563	-4833184749	7392840491	61	pressure	\N	\N	18.45	17.11	2025-12-07 15:20:10.668218	‐	44.075876	58.663883	2025-12-07 10:20:27.687821	\N	\N	\N	\N	\N	received
564	-4833184749	7392840491	89	reagent	1259	1	17.01	16.76	2025-12-07 16:15:43.936664	‐	44.019162	58.722882	2025-12-07 11:15:57.337662	\N	\N	\N	\N	\N	received
565	-4833184749	7392840491	48	other	\N	\N	\N	\N	2025-12-07 16:58:10.165592	Гидраты под манометр [Давления ДО: Труб.=12.8 атм; Лин.=16.7 атм | ПОСЛЕ: Труб.=17.3 атм; Лин.=16.6 атм]	\N	\N	2025-12-07 12:22:07.420701	\N	\N	\N	\N	\N	timeout
566	-4833184749	7392840491	89	reagent	1251	1	18.09	17.55	2025-12-07 21:11:01.425446	‐	44.019086	58.722833	2025-12-07 16:11:13.367092	\N	\N	\N	\N	\N	received
567	-4833184749	7392840491	43	reagent	1251	1	17.25	16.79	2025-12-07 21:30:21.732293	‐	44.050162	58.699692	2025-12-07 16:30:34.721073	\N	\N	\N	\N	\N	received
568	-4833184749	1042194608	48	reagent	1251	1	17.26	16.83	2025-12-07 21:42:18.966077		44.05849	58.686685	2025-12-07 16:43:15.815374	\N	\N	\N	\N	\N	received
569	-4833184749	1042194608	48	reagent	1253	1	17.82	17.13	2025-12-07 22:11:16.225104		44.058455	58.68679	2025-12-07 17:11:27.146522	\N	\N	\N	\N	\N	received
570	-4833184749	1042194608	87	pressure	\N	\N	18.48	16.65	2025-12-07 22:46:16.708872		\N	\N	2025-12-07 17:46:23.42046	\N	\N	\N	\N	\N	skipped_by_user
571	-4833184749	1042194608	61	pressure	\N	\N	18.36	17.16	2025-12-07 22:46:48.536898		\N	\N	2025-12-07 17:46:57.40575	\N	\N	\N	\N	\N	skipped_by_user
572	-4833184749	7392840491	89	reagent	1259	1	18.8	18.45	2025-12-08 09:07:42.176232	‐	44.019137	58.722827	2025-12-08 04:07:55.161284	\N	\N	\N	\N	\N	received
573	-4833184749	7392840491	43	reagent	SW-OF	1	16.76	16.4	2025-12-08 09:20:55.577789	‐	44.050254	58.700028	2025-12-08 04:21:08.755511	\N	\N	\N	\N	\N	received
574	-4833184749	1042194608	48	reagent	Oil Foam	1	16.67	16.31	2025-12-08 09:32:17.933655		44.058407	58.686868	2025-12-08 04:32:28.106238	\N	\N	\N	\N	\N	received
575	-4833184749	1042194608	87	pressure	\N	\N	18.19	16.44	2025-12-08 09:47:01.199039		44.05993	58.696367	2025-12-08 04:47:13.309029	\N	\N	\N	\N	\N	received
\.


--
-- Data for Name: group_messages; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.group_messages (id, chat_id, message_id, date_ts, from_user_id, from_user_name, content_type, text, caption, media_group_id, photo_file_id, photo_unique_id, video_file_id, video_unique_id, audio_file_id, audio_unique_id, voice_file_id, voice_unique_id, document_file_id, document_unique_id, extra_json) FROM stdin;
\.


--
-- Data for Name: reagent_supplies; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.reagent_supplies (id, reagent, qty, unit, received_at, source, location, comment) FROM stdin;
1	1259	100.000	шт	2025-12-07 15:54:47.350521+00	\N	\N	\N
2	1251	50.000	шт	2025-12-04 18:13:00+00	\N	\N	\N
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.users (id, username, full_name, first_seen) FROM stdin;
484694023	\N	Никитин Владимир	2025-11-04 22:49:51.489371
113294156	hvan_maks	Khvan Maksim	2025-11-06 03:57:02.843998
5847521893	komptelegram	Серик Каракаев	2025-11-05 13:37:40.99112
6740275295	\N	Аза	2025-11-10 09:11:49.278498
1042194608	avazbek_kabilov	Авазбек Кабылов	2025-11-05 13:39:45.218698
6238913206	\N	𝐾𝑖𝑑𝑖𝑟𝑏𝑎𝑦 𝐽𝑎𝑦𝑙𝑖𝑏𝑎𝑒𝑣𝑖𝑐ℎ	2025-11-05 08:40:10.526384
6730772526	skarakaev	Серик Каракаев	2025-11-05 06:00:40.262578
7392840491	Xayrulla85	Хайрулла	2025-11-06 03:55:30.441097
\.


--
-- Data for Name: well_channels; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_channels (id, well_id, channel, started_at, ended_at, note, created_at, updated_at) FROM stdin;
6	1	1	2025-11-03 11:52:00	2025-11-19 11:52:00	\N	2025-11-25 09:52:03.694662	2025-11-25 09:52:35.787279
7	1	1	2025-11-19 11:52:00	\N	\N	2025-11-25 09:52:35.787279	2025-11-25 09:52:35.787279
11	6	3	2025-11-06 17:50:00	\N	\N	2025-11-27 09:01:23.016285	2025-11-27 09:01:23.016285
12	3	4	2025-10-29 21:55:00	\N	\N	2025-11-27 09:04:49.759954	2025-11-27 09:04:49.759954
13	2	2	2025-10-17 15:33:00	2025-11-07 10:10:00	\N	2025-11-27 09:08:13.559269	2025-11-27 09:08:13.559269
14	4	5	2025-10-02 19:20:00	2025-10-29 19:10:00	\N	2025-11-27 09:13:04.859037	2025-11-27 09:14:07.150369
15	4	5	2025-10-31 22:30:00	\N	\N	2025-11-27 09:13:29.763234	2025-11-27 09:14:15.959638
9	7	2	2025-11-07 10:10:00	\N	\N	2025-11-25 11:03:38.609818	2025-11-27 10:01:41.494534
\.


--
-- Data for Name: well_construction; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_construction (id, well_no, horizon, prod_casing_diam_mm, prod_casing_depth_m, current_bottomhole_m, perf_intervals_m, tubing_diam_mm, tubing_shoe_depth_m, packer_depth_m, adapter_depth_m, pattern_stuck_depth_m, choke_diam_mm, created_at, updated_at, data_as_of) FROM stdin;
1	38	J2-7	140.00	2950.00	2925.00	\N	73.00	2451.98	2420.30	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
2	45	J2-7	140.00	2950.00	2800.00	\N	73.00	2439.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
3	64	J2-1\nJ2-1a\nJ2-2\nJ2-3	140.00	2950.00	2358.00	\N	NaN	2223.87	2211.38	1206.37	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
4	69	J3-6\nJ2-1a\nJ2-2\nJ2-3\nJ2-4\nJ2-5	140.00	2950.00	2450.00	\N	73.00	2223.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
5	70	J2-2\nJ2-3\nJ2-4\nJ2-5\nJ2-6\nJ2-7	140.00	2950.00	2495.00	\N	73.00	2228.20	2198.85	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
6	74	J2-3\nJ2-4	140.00	2950.00	2347.00	\N	73.00	2276.00	2246.00	NaN	2180.00	11.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
7	109	J2-1a\nJ2-2\nJ2-3\nJ2-4	140.00	2950.00	2385.00	\N	73.00	2206.97	2197.08	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
8	115	J2-2\nJ2-3\nJ2-4	140.00	2950.00	2917.00	\N	73.00	2235.00	2206.11	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
9	118	J3-6	140.00	2950.00	2145.00	\N	73.00	2015.09	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
10	102	J3-7\nJ2-1\nJ2-1a\nJ2-2\nJ2-3\nJ2-4	140.00	2950.00	2947.00	\N	73.00	2095.00	2085.00	NaN	2077.00	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
11	104	J2-5\nJ2-6\nJ2-7\nJ2-8	140.00	2950.00	2920.00	\N	73.00	2377.56	2357.56	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
12	142	J2-1\nJ2-1a\nJ2-2\nJ2-3\nJ2-4	140.00	2950.00	2364.00	\N	73.00	2140.79	2130.79	NaN	2358.00	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
13	35	J2-5\nJ2-6	140.00	2950.00	2505.00	\N	73.00	2365.00	2335.00	NaN	NaN	9.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
14	36	J2-2	140.00	2950.00	2782.00	\N	73.00	2267.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
15	86	J2-10	140.00	2950.00	2929.00	\N	73.00	2669.31	2638.31	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
16	91	J2-1а\nJ2-3\nJ2-4	140.00	2950.00	2449.00	\N	73.00	2234.00	2204.00	NaN	NaN	9.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
17	98	J2-4	140.00	2950.00	2425.00	\N	73.00	2331.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
18	101	J3-6\nJ3-7\nJ2-1	140.00	2950.00	2215.00	\N	73.00	2026.84	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
19	43	J2-3\nJ2-4\nJ2-5\nJ2-6	140.00	2950.00	2471.00	\N	73.00	2306.50	2296.50	NaN	NaN	8.20	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
20	48	J3-6\nJ3-7\nJ2-1\nJ2-1a	140.00	2950.00	2279.00	\N	73.00	2015.00	1985.00	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
21	68	J3-7\nJ2-1\nJ2-1a\nJ2-2\nJ2-3	140.00	2950.00	2300.00	\N	73.00	2106.35	2077.04	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
22	72	J3-5b\nJ3-6\nJ3-7	140.00	2950.00	2465.00	\N	73.00	1976.87	1947.90	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
23	83	J2-6\nJ2-8	140.00	2950.00	2581.00	\N	73.00	2401.00	2372.00	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
24	87	J2-1a\nJ2-2	140.00	2950.00	2310.00	\N	73.00	2220.00	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
25	120	J3-6\nJ3-7\nJ2-1\nJ2-1a	140.00	2950.00	2198.00	\N	73.00	2056.05	2026.05	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
26	128	J2-6\nJ2-7	140.00	2950.00	2830.00	\N	73.00	2465.77	2436.84	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
27	136	J3-7\nJ2-1\nJ2-1a	140.00	2950.00	2925.00	\N	73.00	2122.00	2092.00	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
28	140	J3-7\nJ2-1\nJ2-2\nJ2-4\nJ2-5\nJ2-7	140.00	2950.00	2488.00	\N	73.00	2485.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
29	13	J3-7	140.00	2950.00	2135.00	\N	73.00	2108.00	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
30	50	J3-5\nJ3-5a	140.00	2950.00	1970.00	\N	73.00	1868.62	NaN	NaN	NaN	11.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
31	71	J3-7	140.00	2950.00	2314.00	\N	73.00	2020.89	1991.52	NaN	1871.00	14.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
32	73	J3-6	140.00	2950.00	2060.00	\N	73.00	2042.00	2012.81	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
33	75	J3-5b\nJ3-6\nJ3-7	140.00	2950.00	2400.00	\N	73.00	1991.71	NaN	NaN	NaN	11.50	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
34	78	J2-4\nJ2-5	140.00	2950.00	2503.00	\N	73.00	2332.00	2302.00	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
35	85	J3-7\nJ2-1\nJ2-1a\nJ2-2	140.00	2950.00	2385.00	\N	73.00	2115.22	2104.38	NaN	2105.00	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
36	89	J3-6	140.00	2950.00	2245.60	\N	73.00	2002.00	NaN	NaN	NaN	11.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
37	100	J3-1	140.00	2950.00	2290.00	\N	73.00	1584.46	NaN	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
38	119	J3-5a	140.00	2950.00	2925.00	\N	73.00	1890.00	NaN	NaN	NaN	14.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
39	127	J3-2	140.00	2950.00	1780.00	\N	73.00	1640.00	1609.00	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
40	129	J3-3a\nJ3-4\nJ3-5\nJ3-5a\nJ3-5b\nJ3-6	140.00	2950.00	2090.00	\N	73.00	1667.26	1657.26	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
41	134	J3-2	140.00	2950.00	2925.00	\N	73.00	1653.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
42	76	J3-6\nJ3-7\nJ2-1\nJ2-1a\nJ2-2\nJ2-3	140.00	2950.00	2923.00	\N	73.00	2061.35	2051.66	NaN	1925.00	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
43	121	J3-6	140.00	2950.00	2480.00	\N	73.00	1950.00	1920.00	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
44	131	J3-5\nJ3-5a\nJ3-5b\nJ3-6\nJ2-4\nJ2-11	140.00	2950.00	2925.00	\N	73.00	2574.64	NaN	NaN	2618.50	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
45	137	J3-6\nJ3-7\nJ2-1\nJ2-1a\nJ2-2	140.00	2950.00	2340.00	\N	73.00	2114.00	2104.00	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
46	138	J2-2\nJ2-3\nJ2-4\nJ2-5	140.00	2950.00	2866.00	\N	73.00	2281.97	2251.95	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
47	20	J2-4	140.00	2950.00	2400.00	\N	73.00	2346.00	NaN	NaN	580.00	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
48	56	J2-10	140.00	2950.00	2924.00	\N	73.00	2746.00	NaN	NaN	758.00	14.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
49	58	J2-10	140.00	2950.00	2827.00	\N	73.00	2707.00	2677.00	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
50	60	J2-2	140.00	2950.00	2921.00	\N	73.00	2292.00	NaN	NaN	NaN	8.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
51	80	J2-2	140.00	2950.00	2926.00	\N	73.00	2334.00	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
52	94	J3-6	140.00	2950.00	2200.00	\N	73.00	2048.49	NaN	NaN	NaN	10.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
53	96	J2-5\nJ2-6	140.00	2950.00	2925.00	\N	73.00	2366.70	NaN	NaN	670.00	14.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
54	116	J2-3\nJ2-4\nJ2-5\nJ2-6	140.00	2950.00	2600.00	\N	73.00	2270.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
55	141	J2-7	140.00	2950.00	2927.00	\N	73.00	2483.00	NaN	NaN	NaN	12.00	2025-11-27 08:51:06.072725+00	2025-11-27 08:51:06.072725+00	2025-11-27
\.


--
-- Data for Name: well_equipment; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_equipment (id, well_id, type_code, serial_number, channel, installed_at, removed_at, note, created_at, updated_at) FROM stdin;
19	6	wellhead_gateway	\N	\N	2025-11-12 10:59:00	\N	Снят с скважины 107	2025-11-27 09:00:11.542915	2025-11-27 09:00:39.131951
17	6	wellhead_sensor	\N	\N	2025-11-06 17:50:00	\N	Снят со скважины 140	2025-11-27 08:59:00.338806	2025-11-27 09:00:45.168482
18	6	line_sensor	\N	\N	2025-11-06 17:50:00	\N	Снят со скважины 140	2025-11-27 08:59:34.749908	2025-11-27 09:00:51.135212
12	3	wellhead_gateway	\N	\N	2025-11-04 00:29:00	\N	\N	2025-11-26 22:29:24.132764	2025-11-27 09:02:57.827396
14	3	line_sensor	\N	\N	2025-10-29 21:55:00	\N	Снят со скважины 30	2025-11-26 22:29:44.82635	2025-11-27 09:03:32.433286
20	3	wellhead_sensor	\N	\N	2025-10-29 21:55:00	\N	Снят со скважины 30	2025-11-27 09:04:12.469007	2025-11-27 09:04:12.46901
11	1	wellhead_gateway	\N	\N	2025-09-04 15:00:00	\N	Снят со скважины 85	2025-11-26 07:28:22.514589	2025-11-27 09:05:48.372835
10	1	line_sensor	\N	\N	2025-10-02 15:00:00	\N	Снят со скважины 85	2025-11-26 07:28:11.326972	2025-11-27 09:06:22.714588
9	1	wellhead_sensor	\N	\N	2025-10-02 15:00:00	\N	Снят со скважины 85	2025-11-26 07:27:55.872772	2025-11-27 09:06:41.364696
21	2	wellhead_sensor	\N	\N	2025-10-17 15:33:00	2025-11-07 10:10:00	Снят со скважины 117	2025-11-27 09:09:15.641797	2025-11-27 09:09:15.641801
22	2	line_sensor	\N	\N	2025-10-17 15:33:00	2025-11-07 10:10:00	Снят со скважины 117	2025-11-27 09:10:00.018172	2025-11-27 09:10:00.018175
23	2	wellhead_gateway	\N	\N	2025-10-18 11:10:00	2025-11-07 10:10:00	Снят со скважины 117	2025-11-27 09:10:51.388232	2025-11-27 09:10:51.388234
25	4	wellhead_sensor	\N	\N	2025-10-31 22:30:00	\N	\N	2025-11-27 09:15:59.109001	2025-11-27 09:15:59.109012
27	4	line_sensor	\N	\N	2025-10-31 22:30:00	\N	\N	2025-11-27 09:17:17.925974	2025-11-27 09:17:17.925983
16	4	wellhead_gateway	\N	\N	2025-10-12 02:14:00	\N	Снят со скважины 85	2025-11-27 00:14:48.033801	2025-11-27 09:17:50.549436
24	4	wellhead_sensor	\N	\N	2025-10-02 19:20:00	2025-10-29 19:10:00	Снят со скважины 142	2025-11-27 09:15:18.676512	2025-11-27 09:19:19.215504
26	4	line_sensor	\N	\N	2025-10-02 19:20:00	2025-10-29 19:10:00	Снят со скважины 142	2025-11-27 09:16:50.08072	2025-11-27 09:19:25.583848
28	7	wellhead_sensor	\N	\N	2025-11-07 10:10:00	\N	Снято со скважины 107	2025-11-27 10:00:41.377355	2025-11-27 10:00:41.377358
29	7	line_sensor	\N	\N	2025-11-07 10:10:00	\N	Снято со скважины 107	2025-11-27 10:01:16.11367	2025-11-27 10:01:16.113673
31	7	wellhead_gateway	\N	\N	2025-11-15 07:22:00	\N	\N	2025-12-03 05:22:59.851825	2025-12-03 05:22:59.851839
\.


--
-- Data for Name: well_notes; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_notes (id, well_id, note_time, text, created_at, updated_at) FROM stdin;
16	3	2025-11-28 00:40:00	Гидраты на штуцере в связи с понижение температуры, отсутствует метанольница	2025-11-28 22:41:40.182507	2025-11-28 22:41:40.182509
17	1	2025-11-26 21:59:00	В связи с переходом с мини ДКС на большой ДКС 3-го ГСП давления на шлейфе выросло с 16 кгс/см2 до 19 кгс/см2 скважина остановилась, открываем на продувку	2025-11-28 22:48:04.714137	2025-11-28 22:48:04.714141
19	6	2025-12-03 23:04:00	Ухудшенная реакция на вброс реагентов Oil Foam	2025-12-04 11:45:38.23426	2025-12-04 11:45:38.234264
20	6	2025-12-05 13:26:00	Рекомендовано заменить скважину.	2025-12-05 11:27:04.664916	2025-12-05 11:27:04.66492
21	6	2025-12-06 09:25:00	Обмерзают манометры	2025-12-06 07:26:52.866045	2025-12-06 07:26:52.866048
22	6	2025-12-06 13:16:00	Произвели утепление манометров	2025-12-06 11:17:18.846975	2025-12-06 11:17:18.846978
23	4	2025-12-06 13:17:00	Произвели утепление манометров	2025-12-06 11:17:43.154599	2025-12-06 11:17:43.154602
\.


--
-- Data for Name: well_perforation_interval; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_perforation_interval (id, well_construction_id, interval_index, top_depth_m, bottom_depth_m) FROM stdin;
1	1	1	2453.00	2456.00
2	1	2	2458.00	2461.00
3	2	1	2442.00	2450.00
4	3	1	2216.60	2218.60
5	3	2	2233.20	2235.20
6	3	3	2238.00	2240.00
7	3	4	2247.00	2250.00
8	3	5	2276.20	2280.20
9	3	6	2286.60	2290.60
10	3	7	2300.00	2302.00
11	3	8	2344.00	2347.00
12	3	9	2350.20	2354.40
13	4	1	2043.00	2054.00
14	4	2	2068.00	2078.00
15	4	3	2085.00	2089.00
16	4	4	2094.00	2099.00
17	4	5	2229.00	2231.00
18	4	6	2234.00	2236.00
19	4	7	2240.00	2245.00
20	4	8	2251.00	2254.00
21	4	9	2259.00	2262.00
22	4	10	2273.00	2280.00
23	4	11	2283.00	2287.00
24	4	12	2294.00	2296.00
25	4	13	2299.00	2305.00
26	4	14	2329.00	2334.00
27	4	15	2342.60	2344.60
28	4	16	2348.00	2350.00
29	4	17	2357.00	2364.00
30	4	18	2369.00	2372.00
31	4	19	2431.00	2436.00
32	5	1	2229.40	2231.40
33	5	2	2233.40	2235.40
34	5	3	2273.50	2275.50
35	5	4	2282.80	2284.80
36	5	5	2286.00	2289.00
37	5	6	2314.00	2316.00
38	5	7	2321.00	2323.00
39	5	8	2333.50	2336.50
40	5	9	2340.00	2342.00
41	5	10	2346.20	2354.20
42	5	11	2355.50	2357.50
43	5	12	2359.20	2362.20
44	5	13	2371.00	2373.00
45	5	14	2380.40	2382.40
46	5	15	2389.70	2393.70
47	5	16	2394.60	2396.60
48	5	17	2397.60	2399.60
49	5	18	2403.80	2408.80
50	5	19	2407.20	2409.20
51	5	20	2409.80	2413.80
52	5	21	2444.00	2446.00
53	5	22	2453.00	2457.00
54	5	23	2461.00	2463.00
55	6	1	2276.50	2285.50
56	6	2	2303.00	2310.00
57	6	3	2325.00	2329.00
58	7	1	2202.00	2209.00
59	7	2	2210.00	2215.00
60	7	3	2217.50	2219.50
61	7	4	2221.00	2223.00
62	7	5	2225.00	2228.00
63	7	6	2229.00	2245.00
64	7	7	2247.50	2249.50
65	7	8	2251.00	2261.00
66	7	9	2265.00	2267.00
67	7	10	2273.00	2275.00
68	7	11	2278.00	2280.00
69	7	12	2283.00	2285.00
70	7	13	2289.00	2293.00
71	7	14	2297.00	2300.00
72	7	15	2303.00	2305.00
73	7	16	2306.00	2311.00
74	7	17	2312.00	2314.00
75	8	1	2239.00	2244.00
76	8	2	2246.00	2248.00
77	8	3	2253.00	2256.00
78	8	4	2261.00	2271.00
79	8	5	2275.00	2278.00
80	8	6	2285.00	2287.00
81	8	7	2288.00	2290.00
82	8	8	2292.00	2294.00
83	8	9	2324.50	2326.50
84	8	10	2335.00	2346.00
85	9	1	2015.10	2017.10
86	9	2	2019.10	2021.10
87	9	3	2033.30	2036.30
88	9	4	2037.00	2039.00
89	9	5	2067.10	2071.10
90	9	6	2092.20	2099.20
91	9	7	2104.20	2109.20
92	10	1	2096.00	2099.00
93	10	2	2148.00	2151.00
94	10	3	2190.00	2193.00
95	10	4	2195.00	2198.00
96	10	5	2220.00	2223.00
97	10	6	2238.50	2240.50
98	10	7	2253.30	2255.30
99	10	8	2258.00	2260.00
100	10	9	2270.00	2274.00
101	10	10	2294.70	2296.70
102	10	11	2326.40	2328.40
103	10	12	2349.80	2351.80
104	11	1	2380.00	2384.00
105	11	2	2394.60	2397.60
106	11	3	2420.60	2423.60
107	11	4	2439.00	2441.00
108	11	5	2454.70	2456.70
109	11	6	2457.50	2460.50
110	11	7	2461.20	2464.20
111	11	8	2473.60	2475.60
112	11	9	2507.70	2510.70
113	12	1	2141.80	2143.80
114	12	2	2174.50	2180.50
115	12	3	2208.00	2211.00
116	12	4	2241.20	2243.20
117	12	5	2252.70	2254.70
118	12	6	2265.50	2267.50
119	12	7	2275.00	2278.00
120	12	8	2291.00	2293.00
121	12	9	2314.80	2316.80
122	12	10	2335.00	2337.00
123	12	11	2340.30	2342.30
124	12	12	2347.30	2350.30
125	12	13	2357.00	2359.00
126	13	1	2365.00	2367.00
127	13	2	2375.00	2379.00
128	13	3	2381.50	2392.50
129	13	4	2412.50	2415.50
130	13	5	2421.00	2423.00
131	13	6	2429.00	2431.00
132	13	7	2437.50	2444.50
133	13	8	2449.00	2451.00
134	14	1	2274.00	2278.00
135	15	1	2674.00	2676.00
136	15	2	2678.00	2686.00
137	15	3	2696.50	2701.50
138	16	1	2235.00	2241.00
139	16	2	2246.00	2249.00
140	16	3	2254.00	2264.00
141	16	4	2301.00	2307.00
142	16	5	2323.00	2325.00
143	16	6	2339.00	2342.00
144	16	7	2351.00	2353.00
145	16	8	2357.00	2360.00
146	17	1	2332.00	2349.00
147	17	2	2352.00	2364.00
148	18	1	2027.00	2030.00
149	18	2	2053.00	2063.00
150	18	3	2070.00	2074.00
151	18	4	2116.50	2119.50
152	18	5	2122.50	2125.50
153	18	6	2131.50	2133.50
154	18	7	2146.00	2150.00
155	19	1	2307.50	2311.50
156	19	2	2311.60	2315.60
157	19	3	2339.10	2343.10
158	19	4	2348.60	2352.60
159	19	5	2374.00	2377.00
160	19	6	2394.00	2398.00
161	19	7	2400.70	2404.70
162	19	8	2418.00	2421.00
163	19	9	2446.00	2449.00
164	20	1	2015.00	2017.00
165	20	2	2030.00	2032.00
166	20	3	2033.00	2035.00
167	20	4	2050.60	2053.60
168	20	5	2056.50	2058.50
169	20	6	2064.40	2069.40
170	20	7	2076.30	2078.30
171	20	8	2084.60	2086.60
172	20	9	2090.70	2092.70
173	20	10	2093.70	2095.70
174	20	11	2100.60	2102.60
175	20	12	2113.20	2115.20
176	20	13	2138.00	2140.00
177	20	14	2181.00	2183.00
178	20	15	2186.60	2188.60
179	20	16	2196.10	2198.10
180	20	17	2199.50	2201.50
181	20	18	2211.30	2213.30
182	20	19	2231.00	2233.00
183	21	1	2106.00	2108.00
184	21	2	2135.00	2137.00
185	21	3	2139.00	2144.00
186	21	4	2175.00	2179.00
187	21	5	2233.60	2238.60
188	21	6	2271.20	2274.20
189	22	1	1981.30	1985.30
190	22	2	2010.30	2014.30
191	22	3	2031.00	2033.00
192	22	4	2036.80	2039.80
193	22	5	2064.50	2068.50
194	22	6	2069.00	2073.00
195	22	7	2076.30	2080.30
196	23	1	2407.00	2413.00
197	23	2	2420.00	2430.00
198	23	3	2432.00	2435.00
199	23	4	2478.00	2491.00
200	23	5	2492.00	2498.00
201	24	1	2222.50	2225.50
202	24	2	2234.20	2237.20
203	24	3	2275.30	2277.30
204	25	1	2056.80	2059.80
205	25	2	2061.90	2066.90
206	25	3	2091.00	2109.00
207	25	4	2137.60	2142.60
208	25	5	2160.70	2162.70
209	25	6	2176.30	2180.30
210	25	7	2188.80	2190.80
211	25	8	2192.50	2195.50
212	26	1	2466.00	2472.00
213	26	2	2481.50	2483.50
214	26	3	2501.00	2503.00
215	27	1	2129.30	2131.30
216	27	2	2141.70	2144.70
217	27	3	2192.00	2201.00
218	27	4	2243.50	2246.50
219	28	1	2137.30	2141.30
220	28	2	2150.30	2154.30
221	28	3	2171.10	2174.10
222	28	4	2190.30	2192.30
223	28	5	2216.80	2218.80
224	28	6	2237.00	2245.00
225	28	7	2249.00	2262.00
226	28	8	2272.40	2276.40
227	28	9	2321.10	2323.10
228	28	10	2325.90	2327.90
229	28	11	2329.30	2331.30
230	28	12	2332.90	2334.90
231	28	13	2349.90	2351.90
232	28	14	2355.70	2358.70
233	28	15	2373.20	2375.20
234	28	16	2376.60	2378.60
235	28	17	2396.80	2398.80
236	28	18	2422.00	2426.00
237	28	19	2433.00	2438.00
238	28	20	2440.60	2442.60
239	28	21	2444.50	2446.50
240	28	22	2457.00	2459.00
241	28	23	2461.50	2464.50
242	28	24	2482.50	2485.50
243	29	1	2110.00	2122.00
244	30	1	1873.00	1877.00
245	30	2	1917.50	1920.50
246	31	1	2029.00	2034.00
247	31	2	2059.00	2064.00
248	31	3	2101.00	2104.00
249	32	1	2042.00	2045.00
250	33	1	1991.00	1994.00
251	33	2	2031.00	2037.00
252	33	3	2044.00	2049.00
253	33	4	2062.00	2065.00
254	33	5	2073.00	2077.00
255	33	6	2083.00	2088.00
256	33	7	2094.00	2097.00
257	33	8	2110.50	2112.50
258	33	9	2114.50	2120.50
259	33	10	2124.00	2131.00
260	34	1	2332.00	2335.00
261	34	2	2342.00	2349.00
262	34	3	2354.00	2357.00
263	34	4	2368.00	2370.00
264	34	5	2375.00	2377.00
265	34	6	2379.00	2388.00
266	34	7	2391.00	2393.00
267	34	8	2397.00	2399.00
268	34	9	2419.00	2421.00
269	35	1	2115.30	2118.30
270	35	2	2153.70	2157.70
271	35	3	2189.00	2194.00
272	35	4	2215.50	2219.50
273	35	5	2252.00	2256.00
274	35	6	2268.20	2271.20
275	35	7	2321.60	2323.60
276	35	8	2334.70	2339.70
277	36	1	2010.00	2016.00
278	36	2	2036.00	2040.00
279	37	1	1614.00	1618.00
280	37	2	1622.00	1626.00
281	37	3	1630.00	1632.00
282	37	4	1648.00	1650.00
283	38	1	1893.00	1903.00
284	39	1	1643.00	1662.00
285	40	1	1677.20	1679.20
286	40	2	1778.40	1780.40
287	40	3	1819.40	1823.40
288	40	4	1845.40	1848.80
289	40	5	1849.60	1851.60
290	40	6	1852.70	1853.70
291	40	7	1857.40	1859.40
292	40	8	1866.30	1868.30
293	40	9	1870.70	1878.70
294	40	10	1881.20	1883.20
295	40	11	1889.60	1992.60
296	40	12	1900.40	1904.40
297	40	13	1985.30	1987.30
298	40	14	1988.00	1990.00
299	40	15	2030.00	2032.00
300	40	16	2038.00	2040.00
301	40	17	2051.00	2053.00
302	40	18	2062.00	2065.00
303	40	19	2067.00	2069.00
304	40	20	2077.00	2083.00
305	41	1	1653.00	1665.00
306	42	1	2062.00	2065.00
307	42	2	2070.20	2076.20
308	42	3	2083.60	2087.60
309	42	4	2103.70	2113.70
310	42	5	2157.50	2159.50
311	42	6	2181.00	2184.00
312	42	7	2193.00	2199.00
313	42	8	2201.00	2204.00
314	42	9	2208.00	2211.00
315	42	10	2224.00	2230.00
316	43	1	1950.00	1952.00
317	43	2	1953.50	1956.50
318	43	3	1964.00	1969.00
319	43	4	2013.20	2017.20
320	43	5	2034.50	2036.50
321	43	6	2061.90	2066.90
322	43	7	2070.00	2075.00
323	43	8	2083.00	2086.00
324	43	9	2087.00	2091.00
325	44	1	1929.90	1931.90
326	44	2	1932.20	1935.20
327	44	3	1944.50	1946.50
328	44	4	1982.40	1985.40
329	44	5	1987.00	1989.00
330	44	6	1991.00	1994.00
331	44	7	2029.00	2035.00
332	44	8	2050.00	2056.00
333	44	9	2146.20	2148.20
334	44	10	2156.00	2159.00
335	44	11	2357.00	2364.00
336	44	12	2368.00	2379.00
337	44	13	2874.00	2882.00
338	45	1	2118.00	2121.00
339	45	2	2123.30	2125.30
340	45	3	2140.30	2144.30
341	45	4	2150.50	2153.50
342	45	5	2199.50	2201.50
343	45	6	2204.50	2206.50
344	45	7	2258.00	2261.00
345	45	8	2270.00	2272.00
346	45	9	2285.00	2287.00
347	45	10	2290.50	2293.50
348	46	1	2282.50	2286.50
349	46	2	2300.00	2302.00
350	46	3	2303.50	2306.50
351	46	4	2319.50	2321.50
352	46	5	2333.00	2336.00
353	46	6	2343.50	2345.50
354	46	7	2366.00	2369.00
355	46	8	2375.00	2385.00
356	46	9	2399.50	2401.50
357	47	1	2353.00	2360.00
358	48	1	2718.00	2736.00
359	48	2	2744.00	2749.00
360	49	1	2711.00	2728.00
361	49	2	2742.00	2750.00
362	50	1	2292.00	2302.00
363	50	2	2306.00	2311.00
364	51	1	2326.00	2336.00
365	52	1	2049.00	2052.00
366	52	2	2076.00	2082.00
367	53	1	2367.00	2370.00
368	53	2	2401.00	2410.00
369	53	3	2425.00	2427.00
370	53	4	2429.00	2447.00
371	53	5	2448.00	2452.00
372	53	6	2454.00	2457.00
373	54	1	2273.00	2284.00
374	54	2	2310.00	2320.00
375	54	3	2322.00	2340.00
376	54	4	2351.00	2355.00
377	54	5	2357.00	2361.00
378	54	6	2387.00	2390.00
379	54	7	2400.00	2403.00
380	54	8	2405.00	2411.00
381	54	9	2412.00	2418.00
382	54	10	2431.00	2434.00
383	54	11	2446.00	2449.00
384	54	12	2458.00	2462.00
385	55	1	2490.00	2499.00
\.


--
-- Data for Name: well_status; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.well_status (id, well_id, status, dt_start, dt_end, note) FROM stdin;
7	4	Адаптация	2025-10-24 09:00:00+00	2025-10-31 10:31:00+00	\r\n          \r\n          
8	4	Наблюдение	2025-10-09 10:31:00+00	2025-10-24 10:31:00+00	\r\n          
6	4	Оптимизация	2025-11-01 09:00:00+00	\N	\r\n          \r\n          \r\n          
17	2	Не обслуживается	2025-11-20 10:00:29.901891+00	\N	\r\n          
18	5	Не обслуживается	2025-11-20 10:01:34.389906+00	\N	\r\n          
14	1	Наблюдение	2025-10-02 15:00:00+00	2025-10-05 11:40:00+00	\r\n          \r\n          
15	1	Адаптация	2025-10-05 09:12:00+00	2025-10-15 11:41:00+00	\r\n          \r\n          \r\n          
12	6	Наблюдение	2025-11-06 17:50:00+00	2025-11-10 07:00:00+00	\r\n          \r\n          
25	6	Адаптация	2025-11-10 07:00:00+00	2025-11-20 07:00:00+00	\r\n          
26	6	Оптимизация	2025-11-20 07:00:00+00	\N	\r\n          
21	7	Наблюдение	2025-11-07 10:10:00+00	2025-11-10 07:00:00+00	\r\n          \r\n          
27	7	Адаптация	2025-11-26 08:32:01+00	2025-11-20 07:00:00+00	\r\n          
24	7	Оптимизация	2025-11-20 07:00:00+00	\N	\r\n          \r\n          \r\n          
13	3	Адаптация	2025-11-01 07:00:00+00	2025-11-10 07:00:00+00	\r\n          \r\n          
28	3	Наблюдение	2025-10-29 21:55:00+00	2025-11-01 07:00:00+00	\r\n          \r\n          
29	3	Оптимизация	2025-11-10 07:00:00+00	\N	\r\n          \r\n          
16	1	Оптимизация	2025-10-15 09:13:00+00	\N	    \r\n          \r\n          \r\n          \r\n            
31	8	Не обслуживается	2025-10-01 13:47:00+00	\N	\N
32	9	Не обслуживается	2025-10-01 13:52:00+00	\N	\N
33	10	Не обслуживается	2025-10-01 14:05:00+00	\N	\N
34	14	Не обслуживается	2025-10-01 16:57:00+00	\N	\N
35	13	Не обслуживается	2025-10-01 16:59:00+00	\N	\N
36	12	Не обслуживается	2025-10-01 17:00:00+00	\N	\N
37	11	Не обслуживается	2025-10-01 17:02:00+00	\N	\N
\.


--
-- Data for Name: wells; Type: TABLE DATA; Schema: public; Owner: telegram_events_db_user
--

COPY public.wells (id, number, name, lat, lon, current_status, description) FROM stdin;
5	140	\N	\N	\N	\N	\N
4	61	\N	44.075987	58.663827	\N	\N
1	89	\N	44.019177	58.722794	\N	\N
7	87	\N	44.059827	58.696379	\N	\N
3	43	\N	44.050154	58.699736	\N	\N
6	48	\N	44.058335	58.686703	\N	\N
2	107	\N	44.048819	58.681368	\N	\N
8	120	\N	44.038795	58.692227	\N	\N
9	131	\N	44.036287	58.707693	\N	\N
10	134	\N	44.019549	58.731709	\N	\N
14	85	\N	43.992208	58.719285	\N	\N
13	129	\N	43.98784	58.720941	\N	\N
12	127	\N	43.990403	58.715464	\N	\N
11	117	\N	44.016047	58.730636	\N	\N
\.


--
-- Name: dashboard_login_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.dashboard_login_log_id_seq', 9, true);


--
-- Name: dashboard_users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.dashboard_users_id_seq', 8, true);


--
-- Name: events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.events_id_seq', 575, true);


--
-- Name: group_messages_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.group_messages_id_seq', 1, false);


--
-- Name: reagent_supplies_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.reagent_supplies_id_seq', 2, true);


--
-- Name: well_channels_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_channels_id_seq', 15, true);


--
-- Name: well_construction_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_construction_id_seq', 55, true);


--
-- Name: well_equipment_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_equipment_id_seq', 31, true);


--
-- Name: well_notes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_notes_id_seq', 25, true);


--
-- Name: well_perforation_interval_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_perforation_interval_id_seq', 385, true);


--
-- Name: well_status_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.well_status_id_seq', 37, true);


--
-- Name: wells_id_seq; Type: SEQUENCE SET; Schema: public; Owner: telegram_events_db_user
--

SELECT pg_catalog.setval('public.wells_id_seq', 14, true);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: chats chats_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.chats
    ADD CONSTRAINT chats_pkey PRIMARY KEY (id);


--
-- Name: dashboard_login_log dashboard_login_log_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.dashboard_login_log
    ADD CONSTRAINT dashboard_login_log_pkey PRIMARY KEY (id);


--
-- Name: dashboard_users dashboard_users_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.dashboard_users
    ADD CONSTRAINT dashboard_users_pkey PRIMARY KEY (id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: group_messages group_messages_chat_id_message_id_key; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_chat_id_message_id_key UNIQUE (chat_id, message_id);


--
-- Name: group_messages group_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.group_messages
    ADD CONSTRAINT group_messages_pkey PRIMARY KEY (id);


--
-- Name: reagent_supplies reagent_supplies_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.reagent_supplies
    ADD CONSTRAINT reagent_supplies_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: well_channels well_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_channels
    ADD CONSTRAINT well_channels_pkey PRIMARY KEY (id);


--
-- Name: well_construction well_construction_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_construction
    ADD CONSTRAINT well_construction_pkey PRIMARY KEY (id);


--
-- Name: well_equipment well_equipment_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_equipment
    ADD CONSTRAINT well_equipment_pkey PRIMARY KEY (id);


--
-- Name: well_notes well_notes_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_notes
    ADD CONSTRAINT well_notes_pkey PRIMARY KEY (id);


--
-- Name: well_perforation_interval well_perforation_interval_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_perforation_interval
    ADD CONSTRAINT well_perforation_interval_pkey PRIMARY KEY (id);


--
-- Name: well_status well_status_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_status
    ADD CONSTRAINT well_status_pkey PRIMARY KEY (id);


--
-- Name: wells wells_number_key; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.wells
    ADD CONSTRAINT wells_number_key UNIQUE (number);


--
-- Name: wells wells_pkey; Type: CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.wells
    ADD CONSTRAINT wells_pkey PRIMARY KEY (id);


--
-- Name: idx_events_event_time; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX idx_events_event_time ON public.events USING btree (event_time);


--
-- Name: idx_events_type_time; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX idx_events_type_time ON public.events USING btree (event_type, event_time);


--
-- Name: idx_events_well; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX idx_events_well ON public.events USING btree (well);


--
-- Name: idx_group_messages_chat; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX idx_group_messages_chat ON public.group_messages USING btree (chat_id, date_ts);


--
-- Name: idx_group_messages_date; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX idx_group_messages_date ON public.group_messages USING btree (date_ts);


--
-- Name: ix_dashboard_users_id; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX ix_dashboard_users_id ON public.dashboard_users USING btree (id);


--
-- Name: ix_dashboard_users_username; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE UNIQUE INDEX ix_dashboard_users_username ON public.dashboard_users USING btree (username);


--
-- Name: ix_well_channels_well_id; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX ix_well_channels_well_id ON public.well_channels USING btree (well_id);


--
-- Name: ix_well_equipment_id; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX ix_well_equipment_id ON public.well_equipment USING btree (id);


--
-- Name: ix_well_equipment_well_id; Type: INDEX; Schema: public; Owner: telegram_events_db_user
--

CREATE INDEX ix_well_equipment_well_id ON public.well_equipment USING btree (well_id);


--
-- Name: dashboard_login_log dashboard_login_log_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.dashboard_login_log
    ADD CONSTRAINT dashboard_login_log_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.dashboard_users(id) ON DELETE CASCADE;


--
-- Name: well_channels well_channels_well_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_channels
    ADD CONSTRAINT well_channels_well_id_fkey FOREIGN KEY (well_id) REFERENCES public.wells(id) ON DELETE CASCADE;


--
-- Name: well_equipment well_equipment_well_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_equipment
    ADD CONSTRAINT well_equipment_well_id_fkey FOREIGN KEY (well_id) REFERENCES public.wells(id);


--
-- Name: well_notes well_notes_well_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_notes
    ADD CONSTRAINT well_notes_well_id_fkey FOREIGN KEY (well_id) REFERENCES public.wells(id) ON DELETE CASCADE;


--
-- Name: well_perforation_interval well_perforation_interval_well_construction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_perforation_interval
    ADD CONSTRAINT well_perforation_interval_well_construction_id_fkey FOREIGN KEY (well_construction_id) REFERENCES public.well_construction(id) ON DELETE CASCADE;


--
-- Name: well_status well_status_well_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: telegram_events_db_user
--

ALTER TABLE ONLY public.well_status
    ADD CONSTRAINT well_status_well_id_fkey FOREIGN KEY (well_id) REFERENCES public.wells(id) ON DELETE CASCADE;


--
-- Name: SCHEMA public; Type: ACL; Schema: -; Owner: telegram_events_db_user
--

REVOKE USAGE ON SCHEMA public FROM PUBLIC;


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON SEQUENCES TO telegram_events_db_user;


--
-- Name: DEFAULT PRIVILEGES FOR TYPES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON TYPES TO telegram_events_db_user;


--
-- Name: DEFAULT PRIVILEGES FOR FUNCTIONS; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON FUNCTIONS TO telegram_events_db_user;


--
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: -; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres GRANT ALL ON TABLES TO telegram_events_db_user;


--
-- PostgreSQL database dump complete
--

\unrestrict bQ9N63d7tkk17AglGJqbHCpScMowaMjCJNVSRSx9cqunk2SElpFXivjsNnctzJS

