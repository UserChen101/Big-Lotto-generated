import random
n=eval(input("请输入随机号码数量："))
for k in range(1,n+1):
    list_red=random.sample(range(1,36),5)   #在【1,35】中获取5个不重复的随机数并将它们存入list_red列表中
    list_blue=random.sample(range(1,13),2)  #在【1,12】中获取2个不重复的随机数并将它们存入list_blue列表中
    print("({:02}) ".format(k),end="")    #输出每行的序号
    for i in list_red:
        print("{:02} ".format(i),end="")    #输出前区号码
    print("+ ",end="")  #前后区间隔符
    for j in list_blue:
        print("{:02} ".format(j),end="")    #输出后去号码
    print() #换行，继续下一组号码