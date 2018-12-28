CREATE TABLE IF NOT EXISTS branch (
   id integer primary key autoincrement,
   url string not null,
   unique(url)
);
CREATE TABLE IF NOT EXISTS merge_proposal (
   id integer primary key autoincrement,
   branch_id integer,
   url string not null,
   foreign key (branch_id) references branch(id)
   unique(url)
);
CREATE TABLE IF NOT EXISTS run (
   command string,
   finish_time integer,
   branch_id integer,
   merge_proposal_id integer,
   foreign key (branch_id) references branch(id),
   foreign key (merge_proposal_id) references merge_proposal(id)
);
